"""
Agent exhaustion checks backed by each agent's real CLI command surface.

The dashboard goal here is intentionally narrow:
- determine whether an agent is exhausted right now
- when the next reset happens, if the CLI output exposes it
"""
from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import subprocess
import time
import fcntl
import struct
import termios
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_OSC_RE = re.compile(r"\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)")
_RESET_RE = re.compile(r"resets?(?:\s+on)?\s+([^|\n\r]+)", re.IGNORECASE)
_CODEX_RESET_ERROR_RE = re.compile(r"try again at ([^.]+)\.", re.IGNORECASE)
_CODEX_STATUS_PANEL_RE = re.compile(
    r"OpenAI Codex.*?Visit https://chatgpt\.com/codex/settings/usage.*?5h limit:.*?(?:Weekly limit:.*?(?:resets [^)]+\)|resets [^\n\r]+))?",
    re.IGNORECASE | re.DOTALL,
)


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_ansi(text: str) -> str:
    text = _OSC_RE.sub("", text)
    text = text.replace("\x1b7", "").replace("\x1b8", "")
    return _ANSI_RE.sub("", text).strip()


def _normalize_terminal_output(text: str) -> str:
    cleaned = _strip_ansi(text)
    cleaned = cleaned.replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _version(command: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    output = _strip_ansi(result.stdout or result.stderr or "")
    return output.splitlines()[0] if output else None


def _parse_json_output(output: str) -> Optional[dict]:
    text = output.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


def _find_reset(text: str) -> Optional[str]:
    match = _RESET_RE.search(text)
    return match.group(1).strip() if match else None


def _normalize_claude_reset(text: str) -> str:
    value = text.strip()
    value = re.sub(r"\(", " (", value, count=1)
    value = re.sub(r"([A-Z][a-z]{2})(\d{1,2})at", r"\1 \2 at ", value)
    value = re.sub(r"(\d)([A-Z])", r"\1 \2", value)
    return value.strip()


def _run_interactive_capture(
    command: list[str],
    user_input: str,
    *,
    startup_timeout: float = 4.0,
    idle_timeout: float = 2.0,
    total_timeout: float = 12.0,
    rows: int = 24,
    cols: int = 80,
    ready_patterns: Optional[list[bytes]] = None,
    respond_to_terminal_queries: bool = False,
) -> str:
    master_fd, slave_fd = pty.openpty()
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        env={
            **os.environ,
            "TERM": os.environ.get("TERM", "xterm-256color"),
            "COLUMNS": str(cols),
            "LINES": str(rows),
        },
    )
    os.close(slave_fd)
    chunks: list[bytes] = []
    deadline = time.monotonic() + total_timeout
    ready_markers = ready_patterns or [b"\xe2\x9d\xaf", b"\xe2\x80\xa6"]

    def _respond_to_terminal_query(chunk: bytes) -> None:
        if not respond_to_terminal_queries:
            return
        if b"\x1b[6n" in chunk:
            os.write(master_fd, f"\x1b[{rows};1R".encode("ascii"))
        if b"\x1b[c" in chunk:
            os.write(master_fd, b"\x1b[?1;2c")
        if b"]10;?" in chunk:
            os.write(master_fd, b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\")
        if b"]11;?" in chunk:
            os.write(master_fd, b"\x1b]11;rgb:0000/0000/0000\x1b\\")

    try:
        startup_deadline = time.monotonic() + startup_timeout
        while time.monotonic() < startup_deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if not ready:
                continue
            chunk = os.read(master_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
            _respond_to_terminal_query(chunk)
            if any(marker in chunk for marker in ready_markers):
                break

        os.write(master_fd, user_input.encode("utf-8") + b"\r")

        last_read = time.monotonic()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.25)
            if ready:
                chunk = os.read(master_fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
                _respond_to_terminal_query(chunk)
                last_read = time.monotonic()
                continue
            if time.monotonic() - last_read >= idle_timeout:
                break
    finally:
        try:
            os.write(master_fd, b"\x03")
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        finally:
            os.close(master_fd)

    return _normalize_terminal_output(b"".join(chunks).decode("utf-8", errors="ignore"))


def _extract_claude_usage_windows(output: str) -> list[dict[str, object]]:
    windows: list[dict[str, object]] = []
    current_window: Optional[str] = None
    current_percent: Optional[int] = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        compact = re.sub(r"\s+", "", line)
        compact_lower = compact.lower()
        if compact_lower.startswith("currentsession") or compact_lower.startswith("curretsession"):
            current_window = "current_session"
            current_percent = None
            continue
        if compact_lower.startswith("currentweek"):
            current_window = "current_week"
            current_percent = None
            continue
        if current_window:
            percent_match = re.search(r"(\d{1,3})%\s*used", compact_lower)
            if percent_match:
                current_percent = int(percent_match.group(1))
                continue
            reset_match = re.match(r"res(?:ets?|es)(.+)", compact, re.IGNORECASE)
            if reset_match and current_percent is not None:
                windows.append(
                    {
                        "window": current_window,
                        "used_percentage": current_percent,
                        "reset_at": _normalize_claude_reset(reset_match.group(1)),
                    }
                )
                current_window = None
                current_percent = None
    return windows


@dataclass
class UsagePeriod:
    """Legacy compatibility placeholder."""

    api_calls: int
    tokens_used: int
    cost_usd: float
    start_date: str
    end_date: str


@dataclass
class UsageWindow:
    label: str
    used_percentage: Optional[int] = None
    remaining_percentage: Optional[int] = None
    reset_at: Optional[str] = None
    exhausted: Optional[bool] = None


@dataclass
class AgentStatus:
    name: str
    available: bool
    installed: bool = False
    authenticated: Optional[bool] = None
    exhausted: Optional[bool] = None
    state: str = "unknown"  # ready | exhausted | signed_out | not_installed | unknown
    next_reset_at: Optional[str] = None
    version: Optional[str] = None
    subscription_tier: Optional[str] = None
    auth_method: Optional[str] = None
    account_label: Optional[str] = None
    status_command: Optional[str] = None
    interactive_status_command: Optional[str] = None
    interactive_usage_command: Optional[str] = None
    status_source: Optional[str] = None
    status_details: Optional[str] = None
    notes: Optional[str] = None
    error_message: Optional[str] = None
    last_checked: Optional[str] = None
    credits_remaining: Optional[int] = None
    credits_limit: Optional[int] = None
    rate_limit_remaining: Optional[int] = None
    total_api_calls: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    daily_usage: Optional[UsagePeriod] = None
    weekly_usage: Optional[UsagePeriod] = None
    monthly_usage: Optional[UsagePeriod] = None
    next_reset_date: Optional[str] = None
    billing_cycle: Optional[str] = None
    usage_windows: list[UsageWindow] = field(default_factory=list)


def _missing_binary_status(
    *,
    name: str,
    version_cmd: list[str],
    status_command: str,
    interactive_status_command: Optional[str],
    interactive_usage_command: Optional[str],
    notes: str,
) -> AgentStatus:
    return AgentStatus(
        name=name,
        available=False,
        installed=False,
        authenticated=False,
        exhausted=None,
        state="not_installed",
        version=_version(version_cmd),
        status_command=status_command,
        interactive_status_command=interactive_status_command,
        interactive_usage_command=interactive_usage_command,
        status_source=status_command,
        notes=notes,
        error_message=f"{name} CLI not found",
        last_checked=_checked_at(),
    )


def _set_usage_windows(status: AgentStatus, windows: list[UsageWindow]) -> None:
    status.usage_windows = windows


def _extract_kiro_bonus_window(output: str) -> Optional[UsageWindow]:
    bonus_match = re.search(
        r"Bonus credits:\s*([0-9.]+)/([0-9.]+)\s*credits used, expires in\s*(\d+)\s*days",
        output,
        re.IGNORECASE,
    )
    if not bonus_match:
        return None
    used = float(bonus_match.group(1))
    total = float(bonus_match.group(2))
    if total <= 0:
        return None
    used_percentage = int(round((used / total) * 100))
    expires_in_days = int(bonus_match.group(3))
    return UsageWindow(
        label="Bonus Usage",
        used_percentage=used_percentage,
        remaining_percentage=max(0, 100 - used_percentage),
        reset_at=f"Expires in {expires_in_days} days",
        exhausted=used_percentage >= 100,
    )


def _extract_codex_usage_windows(output: str) -> list[UsageWindow]:
    windows: list[UsageWindow] = []
    normalized_lines = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[│╭╰─•\s]+", "", line).strip()
        line = re.sub(r"[│\s]+$", "", line).strip()
        if line:
            normalized_lines.append(line)

    for index, line in enumerate(normalized_lines):
        short_match = re.match(
            r"5h limit:\s*(?:\[[^\]]+\]\s*)?(\d{1,3})%\s+left(?:\s*\(resets\s+([^)]+)\))?",
            line,
            re.IGNORECASE,
        )
        if short_match:
            remaining = int(short_match.group(1))
            used = max(0, 100 - remaining)
            windows.append(
                UsageWindow(
                    label="5h Usage",
                    used_percentage=used,
                    remaining_percentage=remaining,
                    reset_at=short_match.group(2).strip() if short_match.group(2) else None,
                    exhausted=remaining <= 0,
                )
            )
            continue

        weekly_match = re.match(
            r"Weekly limit:\s*(?:\[[^\]]+\]\s*)?(\d{1,3})%\s+left(?:\s*\(resets\s+([^)]+)\))?",
            line,
            re.IGNORECASE,
        )
        if weekly_match:
            remaining = int(weekly_match.group(1))
            used = max(0, 100 - remaining)
            reset_at = weekly_match.group(2).strip() if weekly_match.group(2) else None
            if reset_at is None and index + 1 < len(normalized_lines):
                next_line = normalized_lines[index + 1]
                next_reset_match = re.match(r"\(resets\s+([^)]+)\)", next_line, re.IGNORECASE)
                if next_reset_match:
                    reset_at = next_reset_match.group(1).strip()
            windows.append(
                UsageWindow(
                    label="Weekly Usage",
                    used_percentage=used,
                    remaining_percentage=remaining,
                    reset_at=reset_at,
                    exhausted=remaining <= 0,
                )
            )
    return windows


def _format_local_reset_timestamp(epoch_seconds: int) -> str:
    local_now = datetime.now().astimezone()
    local_dt = datetime.fromtimestamp(epoch_seconds, timezone.utc).astimezone()
    time_text = local_dt.strftime("%I:%M %p").lstrip("0")
    if local_dt.date() == local_now.date():
        return time_text
    return f"{local_dt.strftime('%b')} {local_dt.day} at {time_text}"


def _read_codex_rate_limits_via_app_server(timeout: float = 8.0) -> tuple[Optional[dict[str, object]], str]:
    process = subprocess.Popen(
        ["codex", "debug", "app-server", "send-message-v2", "."],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: list[str] = []
    json_lines: list[str] = []
    brace_depth = 0
    deadline = time.monotonic() + timeout

    try:
        stdout = process.stdout
        if stdout is None:
            return None, ""

        while time.monotonic() < deadline:
            line = stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue

            lines.append(line)
            stripped = line.lstrip()
            if not stripped.startswith("<"):
                continue

            payload = stripped[1:].lstrip()
            if json_lines:
                json_lines.append(payload)
                brace_depth += payload.count("{") - payload.count("}")
            elif payload.startswith("{"):
                json_lines = [payload]
                brace_depth = payload.count("{") - payload.count("}")
            else:
                continue

            if brace_depth > 0:
                continue

            try:
                message = json.loads("".join(json_lines))
            except json.JSONDecodeError:
                json_lines = []
                brace_depth = 0
                continue

            json_lines = []
            brace_depth = 0
            if message.get("method") != "account/rateLimits/updated":
                continue

            params = message.get("params")
            if not isinstance(params, dict):
                continue
            rate_limits = params.get("rateLimits")
            if isinstance(rate_limits, dict):
                return rate_limits, "".join(lines)
    finally:
        if process.stdout is not None:
            process.stdout.close()
        try:
            process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    return None, "".join(lines)


def _iter_strings(value: object) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            strings.extend(_iter_strings(child))
    elif isinstance(value, list):
        for child in value:
            strings.extend(_iter_strings(child))
    return strings


def _read_codex_status_cache() -> str:
    codex_home = Path.home() / ".codex"
    candidates: list[Path] = [codex_home / "log" / "codex-tui.log"]
    now = time.time()
    if (codex_home / "sessions").exists():
        try:
            candidates.extend(
                sorted((codex_home / "sessions").rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)[:8]
            )
        except OSError:
            pass

    snippets: list[str] = []
    for path in candidates:
        if path.suffix == ".jsonl":
            try:
                raw_lines = path.read_text(errors="ignore").splitlines()[-800:]
            except OSError:
                continue
            for raw_line in reversed(raw_lines):
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                for candidate in _iter_strings(entry.get("payload", {})):
                    if (
                        "5h limit:" not in candidate
                        and "Weekly limit:" not in candidate
                        and "You've hit your usage limit" not in candidate
                        and "Visit https://chatgpt.com/codex/settings/usage" not in candidate
                    ):
                        continue
                    normalized_candidate = _normalize_terminal_output(candidate)
                    panel_matches = list(_CODEX_STATUS_PANEL_RE.finditer(normalized_candidate))
                    if panel_matches:
                        snippets.append(panel_matches[-1].group(0))
                        break
                    snippets.append(normalized_candidate)
                if snippets:
                    break
            if snippets:
                continue

        try:
            if now - path.stat().st_mtime > 900:
                continue
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if (
            "5h limit:" not in text
            and "Weekly limit:" not in text
            and "You've hit your usage limit" not in text
        ):
            continue
        normalized_text = (
            text[-200000:]
            .replace("\\u001b", "\x1b")
            .replace("\\u0007", "\x07")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
        )
        normalized_text = _normalize_terminal_output(normalized_text)
        panel_matches = list(_CODEX_STATUS_PANEL_RE.finditer(normalized_text))
        if panel_matches:
            snippets.append(panel_matches[-1].group(0))
            continue
        snippets.append(normalized_text)
    return "\n".join(snippets)


def check_claude_status() -> AgentStatus:
    """Check Claude using auth status plus interactive /usage."""
    if not shutil.which("claude"):
        return _missing_binary_status(
            name="claude-code",
            version_cmd=["claude", "--version"],
            status_command="claude auth status",
            interactive_status_command="/status",
            interactive_usage_command="/usage",
            notes="Claude exposes plan exhaustion and reset details via `/usage`.",
        )

    status = AgentStatus(
        name="claude-code",
        available=False,
        installed=True,
        version=_version(["claude", "--version"]),
        status_command='claude (interactive) -> /usage',
        interactive_status_command="/status",
        interactive_usage_command="/usage",
        status_source='claude (interactive) -> /usage',
        notes="Claude exposes plan exhaustion and reset details in interactive `/usage`.",
        last_checked=_checked_at(),
    )

    try:
        auth_result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        status.authenticated = False
        status.state = "unknown"
        status.error_message = "Timed out running `claude auth status`"
        return status

    auth_payload = _parse_json_output(auth_result.stdout or auth_result.stderr or "")
    logged_in = bool(auth_payload and auth_payload.get("loggedIn"))
    status.authenticated = logged_in
    status.auth_method = auth_payload.get("authMethod") if auth_payload else None
    status.account_label = (
        auth_payload.get("email") or auth_payload.get("workspaceName") or auth_payload.get("account")
        if auth_payload
        else None
    )

    if not logged_in:
        status.state = "signed_out"
        status.error_message = "Not authenticated"
        return status

    try:
        output = _run_interactive_capture(["claude"], "/usage")
    except (OSError, subprocess.SubprocessError):
        output = ""

    interactive_windows = _extract_claude_usage_windows(output)
    if interactive_windows:
        status.status_details = output or None
        status.status_source = 'claude (interactive) -> /usage'
        _set_usage_windows(
            status,
            [
                UsageWindow(
                    label="5h Usage" if str(window["window"]) == "current_session" else "Weekly Usage",
                    used_percentage=int(window["used_percentage"]),
                    remaining_percentage=max(0, 100 - int(window["used_percentage"])),
                    reset_at=str(window["reset_at"]),
                    exhausted=int(window["used_percentage"]) >= 100,
                )
                for window in interactive_windows
            ],
        )
        exhausted_windows = [window for window in interactive_windows if int(window["used_percentage"]) >= 100]
        selected_window = exhausted_windows[0] if exhausted_windows else interactive_windows[0]
        status.next_reset_at = str(selected_window["reset_at"])
        status.next_reset_date = status.next_reset_at
        status.exhausted = bool(exhausted_windows)
        status.state = "exhausted" if status.exhausted else "ready"
        status.available = not bool(status.exhausted)
        return status

    try:
        usage_result = subprocess.run(
            ["claude", "-p", "/usage"],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except subprocess.TimeoutExpired:
        status.available = True
        status.state = "unknown"
        status.error_message = "Timed out running interactive `/usage` and fallback `claude -p \"/usage\"`"
        return status

    output = _strip_ansi(usage_result.stdout or usage_result.stderr or "")
    status.status_source = 'claude -p "/usage"'
    status.status_details = output or None
    status.next_reset_at = _find_reset(output)
    status.next_reset_date = status.next_reset_at
    if status.next_reset_at:
        _set_usage_windows(status, [UsageWindow(label="Usage", reset_at=status.next_reset_at)])

    if re.search(r"hit your .* limit|usage limit", output, re.IGNORECASE):
        status.exhausted = True
        status.state = "exhausted"
        status.available = False
        return status

    status.available = True
    if status.next_reset_at:
        status.exhausted = False
        status.state = "ready"
    else:
        status.exhausted = None
        status.state = "unknown"
    return status


def check_kiro_status() -> AgentStatus:
    """Check Kiro using whoami plus /usage."""
    if not shutil.which("kiro-cli"):
        return _missing_binary_status(
            name="kiro",
            version_cmd=["kiro-cli", "--version"],
            status_command='kiro-cli chat --no-interactive "/usage"',
            interactive_status_command=None,
            interactive_usage_command="/usage",
            notes="Kiro exposes credits and reset timing via `/usage`.",
        )

    status = AgentStatus(
        name="kiro",
        available=False,
        installed=True,
        version=_version(["kiro-cli", "--version"]) or _version(["kiro-cli", "-V"]),
        status_command='kiro-cli chat --no-interactive "/usage"',
        interactive_usage_command="/usage",
        status_source='kiro-cli chat --no-interactive "/usage"',
        notes="Kiro exposes credits and reset timing via `/usage`.",
        last_checked=_checked_at(),
    )

    try:
        whoami_result = subprocess.run(
            ["kiro-cli", "whoami", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        status.authenticated = False
        status.state = "unknown"
        status.error_message = "Timed out running `kiro-cli whoami --format json`"
        return status

    payload = _parse_json_output(whoami_result.stdout or whoami_result.stderr or "")
    if whoami_result.returncode != 0:
        status.authenticated = False
        status.state = "signed_out"
        status.error_message = _strip_ansi(whoami_result.stderr or whoami_result.stdout or "Not authenticated")
        return status

    status.authenticated = True
    status.auth_method = payload.get("accountType") if payload else None
    status.account_label = payload.get("email") or payload.get("profile") if payload else None

    try:
        usage_result = subprocess.run(
            ["kiro-cli", "chat", "--no-interactive", "/usage"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except subprocess.TimeoutExpired:
        status.available = True
        status.state = "unknown"
        status.error_message = "Timed out running `kiro-cli chat --no-interactive \"/usage\"`"
        return status

    output = _strip_ansi(usage_result.stdout or usage_result.stderr or "")
    status.status_details = output or None
    status.next_reset_at = _find_reset(output)
    status.next_reset_date = status.next_reset_at

    tier_match = re.search(r"\b(KIRO\s+\w+)\b", output, re.IGNORECASE)
    if tier_match:
        status.subscription_tier = tier_match.group(1).upper()

    if "could not retrieve usage information" in output.lower():
        status.available = True
        status.state = "unknown"
        status.error_message = output.splitlines()[0] if output else "Unable to retrieve usage information"
        return status

    plan_percent = None
    percent_match = re.search(r"Credits.*?(\d{1,3})%", output, re.IGNORECASE | re.DOTALL)
    if percent_match:
        plan_percent = int(percent_match.group(1))
        status.usage_windows.append(
            UsageWindow(
                label="Plan Usage",
                used_percentage=plan_percent,
                remaining_percentage=max(0, 100 - plan_percent),
                reset_at=status.next_reset_at,
                exhausted=plan_percent >= 100,
            )
        )

    bonus_window = _extract_kiro_bonus_window(output)
    if bonus_window:
        status.usage_windows.append(bonus_window)

    overages_disabled = "Overages: Disabled" in output
    if plan_percent is not None:
        status.exhausted = plan_percent >= 100 and overages_disabled
        status.state = "exhausted" if status.exhausted else "ready"
        status.available = not bool(status.exhausted)
        return status

    status.available = True
    status.state = "unknown"
    return status


def check_codex_status() -> AgentStatus:
    """Check Codex login state and best-effort /status usage windows."""
    if not shutil.which("codex"):
        return _missing_binary_status(
            name="codex",
            version_cmd=["codex", "--version"],
            status_command="codex login status",
            interactive_status_command="/status",
            interactive_usage_command="/status",
            notes="Codex exposes limit and reset details in the interactive `/status` panel.",
        )

    status = AgentStatus(
        name="codex",
        available=False,
        installed=True,
        version=_version(["codex", "--version"]) or _version(["codex", "-V"]),
        status_command='codex --no-alt-screen (interactive) -> /status',
        interactive_status_command="/status",
        interactive_usage_command="/status",
        status_source='codex --no-alt-screen (interactive) -> /status',
        notes="Codex exposes limit and reset details in the interactive `/status` panel.",
        last_checked=_checked_at(),
    )

    try:
        result = subprocess.run(
            ["codex", "login", "status"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        status.authenticated = False
        status.state = "unknown"
        status.error_message = "Timed out running `codex login status`"
        return status

    output = _strip_ansi(result.stdout or result.stderr or "")
    status.status_details = output or None
    logged_in = result.returncode == 0 and "logged in" in output.lower()
    status.authenticated = logged_in

    if "chatgpt" in output.lower():
        status.auth_method = "ChatGPT"
    elif "api key" in output.lower():
        status.auth_method = "API key"

    if not logged_in:
        status.state = "signed_out"
        status.error_message = output or "Not authenticated"
        return status

    rate_limits, app_server_output = _read_codex_rate_limits_via_app_server()
    if rate_limits:
        primary = rate_limits.get("primary") if isinstance(rate_limits, dict) else None
        secondary = rate_limits.get("secondary") if isinstance(rate_limits, dict) else None
        if isinstance(rate_limits.get("planType"), str):
            status.subscription_tier = str(rate_limits["planType"]).upper()

        windows: list[UsageWindow] = []
        if isinstance(primary, dict):
            used_percent = primary.get("usedPercent")
            resets_at = primary.get("resetsAt")
            if isinstance(used_percent, int):
                windows.append(
                    UsageWindow(
                        label="5h Usage",
                        used_percentage=used_percent,
                        remaining_percentage=max(0, 100 - used_percent),
                        reset_at=_format_local_reset_timestamp(resets_at) if isinstance(resets_at, int) else None,
                        exhausted=used_percent >= 100,
                    )
                )
        if isinstance(secondary, dict):
            used_percent = secondary.get("usedPercent")
            resets_at = secondary.get("resetsAt")
            if isinstance(used_percent, int):
                windows.append(
                    UsageWindow(
                        label="Weekly Usage",
                        used_percentage=used_percent,
                        remaining_percentage=max(0, 100 - used_percent),
                        reset_at=_format_local_reset_timestamp(resets_at) if isinstance(resets_at, int) else None,
                        exhausted=used_percent >= 100,
                    )
                )

        if windows:
            status.status_source = "codex debug app-server send-message-v2"
            status.status_details = None
            _set_usage_windows(status, windows)
            exhausted_windows = [window for window in windows if window.exhausted]
            selected_window = exhausted_windows[0] if exhausted_windows else windows[0]
            status.next_reset_at = selected_window.reset_at
            status.next_reset_date = status.next_reset_at
            status.exhausted = bool(exhausted_windows)
            status.state = "exhausted" if status.exhausted else "ready"
            status.available = not bool(status.exhausted)
            return status

        if app_server_output:
            status.status_details = app_server_output

    try:
        interactive_output = _run_interactive_capture(
            ["codex", "--no-alt-screen"],
            "/status",
            startup_timeout=12.0,
            idle_timeout=6.0,
            total_timeout=30.0,
            rows=40,
            cols=120,
            ready_patterns=[
                b"OpenAI Codex",
                b"? for shortcuts",
                b"gpt-5.4",
                b"gpt-5.5",
                b"gpt-5",
            ],
            respond_to_terminal_queries=True,
        )
    except (OSError, subprocess.SubprocessError):
        interactive_output = ""

    windows = _extract_codex_usage_windows(interactive_output)
    if windows:
        status.status_details = interactive_output or status.status_details
        _set_usage_windows(status, windows)
        exhausted_windows = [window for window in windows if window.exhausted]
        selected_window = exhausted_windows[0] if exhausted_windows else windows[0]
        status.next_reset_at = selected_window.reset_at
        status.next_reset_date = status.next_reset_at
        status.exhausted = bool(exhausted_windows)
        status.state = "exhausted" if status.exhausted else "ready"
        status.available = not bool(status.exhausted)
        return status

    cached_output = _read_codex_status_cache()
    windows = _extract_codex_usage_windows(cached_output)
    if windows:
        status.status_source = "codex cached /status snapshot"
        status.status_details = None
        _set_usage_windows(status, windows)
        exhausted_windows = [window for window in windows if window.exhausted]
        selected_window = exhausted_windows[0] if exhausted_windows else windows[0]
        status.next_reset_at = selected_window.reset_at
        status.next_reset_date = status.next_reset_at
        status.exhausted = bool(exhausted_windows)
        status.state = "exhausted" if status.exhausted else "ready"
        status.available = not bool(status.exhausted)
        return status

    reset_error_matches = _CODEX_RESET_ERROR_RE.findall(cached_output)
    if reset_error_matches:
        status.next_reset_at = reset_error_matches[-1].strip()
        status.next_reset_date = status.next_reset_at

    status.status_source = "codex login status"
    status.available = True
    status.exhausted = None
    status.state = "unknown"
    return status


def check_gemini_status(project_dir: Optional[Path] = None) -> AgentStatus:
    """Check Gemini availability."""
    if not shutil.which("gemini"):
        return _missing_binary_status(
            name="gemini",
            version_cmd=["gemini", "--version"],
            status_command='gemini --help',
            interactive_status_command=None,
            interactive_usage_command=None,
            notes="Gemini CLI is locally installed.",
        )

    status = AgentStatus(
        name="gemini",
        available=True,
        installed=True,
        authenticated=True,
        exhausted=False,
        state="ready",
        version=_version(["gemini", "--version"]),
        status_command="gemini --version",
        status_source="gemini --version",
        notes="Gemini CLI is available.",
        last_checked=_checked_at(),
    )

    # Enrich with local project registry if available
    if project_dir:
        try:
            from sdlc_orchestrator.agent_registry import AgentRegistry
            registry = AgentRegistry(project_dir)
            agent_info = registry.get_agent("gemini")
            if agent_info:
                status.total_tokens = agent_info.total_tokens_used
                status.total_api_calls = agent_info.total_api_calls
                status.total_cost = agent_info.estimated_cost_usd
                
                quota_limit = agent_info.credits_limit or 4_000_000
                used_percentage = min(100, int((status.total_tokens / quota_limit) * 100)) if quota_limit > 0 else 0
                remaining_percentage = max(0, 100 - used_percentage)
                
                status.usage_windows.append(
                    UsageWindow(
                        label="Token Quota",
                        used_percentage=used_percentage,
                        remaining_percentage=remaining_percentage,
                        reset_at="Monthly (Local)",
                        exhausted=used_percentage >= 100
                    )
                )
        except Exception:
            pass

    if not status.next_reset_at:
        status.next_reset_at = "N/A"

    return status


def check_all_agents(project_dir: Optional[Path] = None) -> dict[str, AgentStatus]:
    return {
        "claude-code": check_claude_status(),
        "kiro": check_kiro_status(),
        "gemini": check_gemini_status(project_dir),
        "codex": check_codex_status(),
    }


def get_agent_usage_stats(project_dir=None) -> dict[str, AgentStatus]:
    return check_all_agents(project_dir)


def get_recommended_agent(project_dir: Optional[Path] = None) -> Optional[str]:
    statuses = check_all_agents(project_dir)
    for agent_name in ["claude-code", "kiro", "gemini", "codex"]:
        status = statuses.get(agent_name)
        if status and status.state == "ready":
            return agent_name
    for agent_name in ["claude-code", "kiro", "gemini", "codex"]:
        status = statuses.get(agent_name)
        if status and status.available:
            return agent_name
    return None

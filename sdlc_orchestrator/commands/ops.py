from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
from rich.table import Table

from sdlc_orchestrator.state_machine import Phase, Status, WorkflowState
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.utils import create_symlink, project_slug, sdlc_home
from sdlc_orchestrator.commands import console, require_project

# Maps each approval gate to the branch Claude should poll
# Branch names are project-namespaced: sdlc-$PROJECT-<phase>
# The actual branch name is resolved at runtime using the active project name.
# Maps (phase, status) approval gates to the branch suffix to poll
# Key format: "phase:status"  e.g. "requirement:awaiting_approval"
_GATE_BRANCH_SUFFIXES: dict[str, str] = {
    "requirement:awaiting_approval":    "requirements",
    "design:awaiting_approval":         "design",
    "planning:awaiting_approval":       "plan",
    "implementation:awaiting_approval": "",       # story branch derived from current_story
    "testing:awaiting_approval":        "testing",
    "documentation:awaiting_approval":  "docs",
}


@click.command()
def status():
    """Show current SDLC state (human-readable)."""
    project_dir = require_project()



    wf = WorkflowState(project_dir)
    spec = MemoryManager(project_dir).spec()

    table = Table(
        title=f"SDLC — {spec.get('project_name', project_dir.name)}",
        show_header=False,
    )
    table.add_column("Key", style="dim", width=22)
    table.add_column("Value")

    color = ("yellow" if wf.is_approval_gate()
             else "green" if wf.is_done() else "blue")
    table.add_row("Phase", f"[{color}]{wf.phase.value}:{wf.status.value}[/{color}]")
    table.add_row("", f"[dim]{wf.label()}[/dim]")
    table.add_row("Approval needed", "YES — sdlc state approve" if wf.approval_needed else "no")
    table.add_row("Branch", wf._data.get("current_branch", "main"))
    table.add_row("SDLC home", str(sdlc_home(project_dir)))
    table.add_row("Last updated", wf._data.get("last_updated", "—")[:19])
    if wf.blocked_reason:
        table.add_row("Blocked", f"[red]{wf.blocked_reason}[/red]")
    console.print(table)

    history = wf._data.get("history", [])
    if history:
        console.print("\n[dim]Recent history:[/dim]")
        for h in history[-6:]:
            console.print(f"  {h['timestamp'][:19].replace('T',' ')}  {h['state']}")

    console.print("\n[dim]To continue: let the Python orchestrator dispatch the next phase agent.[/dim]")


@click.command()
@click.argument("phase")
@click.argument("event")
def notify(phase, event):
    """Send a Slack notification. Called by Claude at approval gates."""
    from sdlc_orchestrator.integrations.slack import notify_from_spec
    project_dir = require_project()
    spec = MemoryManager(project_dir).spec()
    notify_from_spec(spec, phase, event)
    click.echo(f"notified: {phase}/{event}")


_SKILL_ERROR_PATTERNS = [
    r"unknown command",
    r"command not found",
    r"no such skill",
    r"skill not found",
    r"unknown slash command",
    r"sdlc[-_]orchestrate.*not found",
    r"invalid agent",
    r"agent.*not found",
    r"cannot find.*sdlc",
]


def _check_skills_health(executor: str) -> list[str]:
    """Return a list of problem descriptions if skills are missing or broken."""
    import json as _json
    from sdlc_orchestrator.memory import executor_config
    from importlib.resources import files as _files

    _, skills_dir, _ = executor_config(executor)
    problems: list[str] = []

    try:
        pkg_skills = {f.name for f in (_files("sdlc_orchestrator") / "skills").iterdir()}
    except Exception:
        return []

    for skill_name in pkg_skills:
        dest = skills_dir / skill_name
        if not dest.exists():
            problems.append(f"missing skill file: {dest}")
        elif dest.stat().st_size == 0:
            problems.append(f"empty skill file: {dest}")

        if executor == "kiro":
            agent_name = skill_name.replace(".md", "")
            agent_json = Path.home() / ".kiro" / "agents" / f"{agent_name}.json"
            if not agent_json.exists():
                problems.append(f"missing agent JSON: {agent_json}")
            else:
                try:
                    data = _json.loads(agent_json.read_text())
                    if not data.get("prompt"):
                        problems.append(f"invalid agent JSON (no prompt): {agent_json}")
                except Exception:
                    problems.append(f"corrupt agent JSON: {agent_json}")

    return problems


def _scan_logs_for_skill_errors(project_dir: Path) -> list[str]:
    """Scan recent workflow logs for skill-not-found error patterns."""
    import re
    from sdlc_orchestrator.utils import sdlc_home

    logs_dir = sdlc_home(project_dir) / "workflow" / "logs"
    if not logs_dir.exists():
        return []

    patterns = [re.compile(p, re.IGNORECASE) for p in _SKILL_ERROR_PATTERNS]
    hits: list[str] = []

    # Check the 5 most recently modified log files
    log_files = sorted(logs_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)[:5]
    for log_file in log_files:
        try:
            for line in log_file.read_text(errors="replace").splitlines()[-200:]:
                if any(p.search(line) for p in patterns):
                    hits.append(f"{log_file.name}: {line.strip()[:120]}")
                    break  # one hit per file is enough
        except Exception:
            continue

    return hits


def _find_all_project_dirs(scan_dir: Path | None = None) -> list[tuple[Path, str]]:
    """Return (project_dir, project_name) for all projects found.
    Checks ~/.sdlc/projects symlinks first, then scans for worktrees with .sdlc/."""
    from sdlc_orchestrator.utils import get_active_project
    results: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def add_project(path: Path, name: str | None = None) -> None:
        try:
            project_dir = path.resolve()
        except Exception:
            return
        if project_dir.name == ".sdlc":
            project_dir = project_dir.parent
        sdlc_dir = project_dir / ".sdlc"
        has_state = (sdlc_dir / "spec.yaml").exists() or (sdlc_dir / "workflow" / "state.json").exists()
        if not has_state or project_dir in seen:
            return
        seen.add(project_dir)
        results.append((project_dir, get_active_project(project_dir)))

    # 1. ~/.sdlc/projects symlinks
    projects_home = Path.home() / ".sdlc" / "projects"
    if projects_home.exists():
        for link in projects_home.iterdir():
            if not (link.is_symlink() and link.exists()):
                continue
            try:
                add_project(link.resolve(), link.name)
            except Exception:
                continue

    # 2. Scan directory for worktree/* dirs with .sdlc/ state
    if scan_dir and scan_dir.exists():
        for child in scan_dir.iterdir():
            if not child.is_dir():
                continue
            worktrees = child / "worktree"
            if worktrees.exists():
                for wt in worktrees.iterdir():
                    if wt.is_dir() and (wt / ".sdlc").exists():
                        add_project(wt)
            elif (child / ".sdlc").exists():
                add_project(child)

    return results


@click.command()
@click.option("--interval", default=30, show_default=True, help="Seconds between polls")
@click.option("--stale-timeout", default=600, show_default=True,
              help="Seconds before a non-advancing project is considered stuck")
@click.option("--all-projects", is_flag=True, default=True, show_default=True,
              help="Watch all projects under ~/.sdlc/projects (default: on)")
@click.option("--projects-dir", default=None, type=click.Path(exists=True, file_okay=False),
              help="Directory to scan for projects (defaults to parent of cwd)")
@click.option("--fix-skills", is_flag=True, default=False,
              help="Auto-reinstall skills when missing or broken (detected from health check or logs)")
def watch(interval, stale_timeout, all_projects, projects_dir, fix_skills):
    """Watch all projects for PR approvals and stuck agents, trigger automatically."""
    import os
    import time
    from sdlc_orchestrator.integrations.github import get_pr_status, is_available
    from sdlc_orchestrator.integrations.slack import notify_from_spec
    from sdlc_orchestrator.commands.init import install_global_skills
    from sdlc_orchestrator.backend import get_runtime

    # Collect projects to watch
    scan = Path(projects_dir) if projects_dir else Path.cwd().parent
    if all_projects:
        project_entries = _find_all_project_dirs(scan_dir=scan)
        if not project_entries:
            project_dir = require_project()
            project_entries = [(project_dir, None)]
    else:
        project_dir = require_project()
        project_entries = [(project_dir, None)]

    console.print(f"[bold]sdlc watch[/bold] — polling every {interval}s, stale timeout {stale_timeout}s")
    console.print(f"  watching {len(project_entries)} project(s)")
    if fix_skills:
        console.print("  skill auto-fix: [green]enabled[/green]")
    console.print("  Press Ctrl-C to stop.\n")

    # Per-project tracking: {project_dir: {state, state_since, last_pr_status}}
    tracking: dict[Path, dict] = {}
    # Track which executors have already had skills fixed this session to avoid spam
    # {executor: last_fix_time}
    skills_fixed_at: dict[str, float] = {}
    SKILL_FIX_COOLDOWN = 300  # don't re-fix the same executor within 5 minutes

    while True:
        try:
            now = time.time()

            for project_dir, project_name in project_entries:
                try:
                    wf = WorkflowState(project_dir)
                except Exception:
                    continue

                current_state = f"{wf.phase.value}:{wf.status.value}"
                spec = MemoryManager(project_dir).spec()
                repo = spec.get("repo", "")
                label = spec.get("project_name", project_dir.name)

                track = tracking.setdefault(project_dir, {
                    "state": current_state,
                    "state_since": now,
                    "last_pr_status": "",
                })

                # ── skill health check ────────────────────────────────────
                if fix_skills:
                    executor = spec.get("executor", "claude-code")
                    last_fix = skills_fixed_at.get(executor, 0)
                    if now - last_fix >= SKILL_FIX_COOLDOWN:
                        problems = _check_skills_health(executor)
                        if not problems:
                            log_hits = _scan_logs_for_skill_errors(project_dir)
                            if log_hits:
                                problems = log_hits
                        if problems:
                            console.print(f"[yellow]SKILL-FIX[/yellow]  {label} ({executor}): {problems[0]}")
                            console.print(f"  reinstalling skills for [bold]{executor}[/bold] ...")
                            try:
                                install_global_skills(force=True, executor=executor)
                                console.print(f"  [green]✓[/green] skills reinstalled for {executor}")
                            except Exception as e:
                                console.print(f"  [red]skill reinstall failed:[/red] {e}")
                            skills_fixed_at[executor] = now

                # Detect state change — reset stale timer
                if track["state"] != current_state:
                    track["state"] = current_state
                    track["state_since"] = now
                    track["last_pr_status"] = ""

                if wf.is_done():
                    continue

                # ── blocked: notify Slack, don't trigger ──────────────────
                if wf.status == Status.BLOCKED:
                    stale_secs = now - track["state_since"]
                    if stale_secs < interval * 2:  # only notify once
                        console.print(f"[red]BLOCKED[/red]  {label}: {wf.blocked_reason or 'no reason given'}")
                        if repo:
                            try:
                                notify_from_spec(spec, "blocked", "blocked")
                            except Exception:
                                pass
                    continue

                # ── stale: state hasn't changed in stale_timeout ──────────
                stale_secs = now - track["state_since"]
                if stale_secs >= stale_timeout:
                    console.print(f"[yellow]STUCK[/yellow]    {label}: '{current_state}' for {int(stale_secs)}s — triggering agent")
                    result = get_runtime().spawn_for_project(
                        project_dir,
                        trigger="stale_timeout",
                        allow_fallback=bool(spec.get("agent_fallback", True)),
                    )
                    if result is None:
                        console.print(f"  [yellow]{label}: executor has no headless CLI[/yellow]")
                    track["state_since"] = now  # reset to avoid re-triggering immediately
                    continue

                # ── approval gate: poll PR ────────────────────────────────
                if not repo or not is_available():
                    continue

                if not project_name:
                    continue
                active = project_name
                gate_key = f"{wf.phase.value}:{wf.status.value}"
                suffix = _GATE_BRANCH_SUFFIXES.get(gate_key, "")
                if wf.phase.value == "implementation" and wf.current_story:
                    branch = f"sdlc-{active}-{wf.current_story.lower()}"
                elif suffix:
                    branch = f"sdlc-{active}-{suffix}"
                else:
                    branch = ""

                if not branch:
                    continue

                pr_status = get_pr_status(repo, branch) or "not-found"
                if pr_status != track["last_pr_status"]:
                    console.print(f"[dim]{label}[/dim]  {current_state}  PR [{branch}]: [bold]{pr_status}[/bold]")
                    track["last_pr_status"] = pr_status

                if pr_status in ("approved", "merged"):
                    console.print(f"[green]✓ PR approved[/green]  {label} — recording approval event")
                    try:
                        from sdlc_orchestrator.backend import record_approval_event
                        record_approval_event(
                            project_dir,
                            phase=wf.phase.value,
                            source="github_pr_poll",
                            state=pr_status,
                            payload={"branch": branch, "repo": repo},
                        )
                    except Exception:
                        pass
                    track["last_pr_status"] = ""

            time.sleep(interval)

        except KeyboardInterrupt:
            console.print("\n[dim]watch stopped.[/dim]")
            break


@click.command()
@click.option("--port", default=8080, show_default=True)
@click.option("--secret", default="", envvar="SDLC_WEBHOOK_SECRET",
              help="GitHub webhook secret (or set SDLC_WEBHOOK_SECRET env var)")
def webhook(port, secret):
    """Start a GitHub webhook receiver for real-time PR event triggers."""
    import hashlib
    import hmac
    import json as _json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    project_dir = require_project()

    class WebhookHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            if self.path != "/webhook":
                self.send_response(404); self.end_headers(); return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            if secret:
                sig_header = self.headers.get("X-Hub-Signature-256", "")
                expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig_header, expected):
                    self.send_response(401); self.end_headers(); return

            try:
                payload = _json.loads(body)
            except _json.JSONDecodeError:
                self.send_response(400); self.end_headers(); return

            event = self.headers.get("X-GitHub-Event", "")
            if event == "pull_request":
                action = payload.get("action")
                merged = payload.get("pull_request", {}).get("merged", False)
                branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
                if action == "closed" and merged and branch.startswith("sdlc/"):
                    console.print(f"[green]✓ PR merged:[/green] {branch} — recording approval event")
                    try:
                        from sdlc_orchestrator.backend import record_approval_event
                        wf = WorkflowState(project_dir)
                        record_approval_event(
                            project_dir,
                            phase=wf.phase.value,
                            source="github_webhook",
                            state="merged",
                            payload={"branch": branch, "event": event},
                        )
                    except Exception:
                        pass

            elif event == "pull_request_review":
                state = payload.get("review", {}).get("state", "").upper()
                branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
                if state == "APPROVED" and branch.startswith("sdlc/"):
                    console.print(f"[green]✓ PR approved:[/green] {branch} — recording approval event")
                    try:
                        from sdlc_orchestrator.backend import record_approval_event
                        wf = WorkflowState(project_dir)
                        record_approval_event(
                            project_dir,
                            phase=wf.phase.value,
                            source="github_webhook",
                            state="approved",
                            payload={"branch": branch, "event": event},
                        )
                    except Exception:
                        pass

            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            else:
                self.send_response(404); self.end_headers()

    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    console.print(f"[bold]sdlc webhook[/bold] — listening on port {port}")
    console.print(f"  Set GitHub webhook URL to: http://<your-host>:{port}/webhook")
    if secret:
        console.print("  Signature verification: [green]enabled[/green]")
    else:
        console.print("  Signature verification: [yellow]disabled[/yellow]")
    console.print("  Press Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]webhook server stopped.[/dim]")


@click.group()
def tick():
    """Tick-lock helpers — prevent concurrent phase-agent runs."""
    pass


@tick.command("acquire")
def tick_acquire():
    """Acquire the tick lock. Exits non-zero if already locked or state is done."""
    import fcntl
    import os
    import time
    project_dir = require_project()

    wf = WorkflowState(project_dir)
    if wf.is_done():
        click.echo("done: workflow is complete — run 'sdlc project close' to start a new cycle", err=True)
        sys.exit(2)

    lock_path = sdlc_home(project_dir) / "workflow" / "tick.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fh.flush()
        pid = os.getpid()
        lock_path.with_suffix(".pid").write_text(str(pid))
        WorkflowState(project_dir).set_process(pid=pid, last_tick=time.time())
        click.echo("ok: lock acquired")
    except BlockingIOError:
        click.echo("locked: another tick is running", err=True)
        sys.exit(1)


@tick.command("release")
def tick_release():
    """Release the tick lock."""
    project_dir = require_project()
    lock_path = sdlc_home(project_dir) / "workflow" / "tick.lock"
    for p in (lock_path, lock_path.with_suffix(".pid")):
        p.unlink(missing_ok=True)
    click.echo("ok: lock released")


@click.command()
@click.option("--all", "relink_all", is_flag=True)
def relink(relink_all):
    """Rebuild ~/.sdlc/projects/<slug> symlink."""
    from pathlib import Path
    if relink_all:
        projects_dir = Path.home() / ".sdlc" / "projects"
        if not projects_dir.exists():
            console.print("[yellow]No ~/.sdlc/projects/ found.[/yellow]")
            return
        for link in projects_dir.iterdir():
            status_str = "[green]ok[/green]" if (link.is_symlink() and link.exists()) else "[red]broken[/red]"
            target = link.readlink() if link.is_symlink() else "?"
            console.print(f"  {status_str}  {link.name} → {target}")
        return

    project_dir = require_project()
    link = create_symlink(project_dir)
    console.print(f"[green]✓[/green] ~/.sdlc/projects/{project_slug(project_dir)} → {link.resolve()}")

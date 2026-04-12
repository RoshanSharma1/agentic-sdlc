"""Slack notifications via incoming webhook."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Optional


def _first_heading(artifact_path: str) -> str:
    """Return the text of the first ## heading in an artifact file, or ''."""
    if not artifact_path:
        return ""
    try:
        for line in Path(artifact_path).read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                return line.lstrip("# ").strip()
    except Exception:
        pass
    return ""


def send(webhook_url: str, phase: str, event: str,
         project_name: str = "", extra: str = "") -> bool:
    if not webhook_url:
        return False

    icons = {
        "awaiting_approval": ":warning:",
        "phase_started":     ":rocket:",
        "phase_done":        ":white_check_mark:",
        "blocked":           ":x:",
        "done":              ":tada:",
    }
    icon = icons.get(event, ":robot_face:")

    lines = [f"{icon} *SDLC Orchestrator*{' | ' + project_name if project_name else ''}"]
    lines.append(f"Phase: `{phase}` | Event: `{event}`")

    if event == "awaiting_approval":
        lines.append(":bust_in_silhouette: *Human approval required*")
        lines.append("Run `sdlc state approve` to continue.")

        # extra is the artifact path when called from state_machine notifier
        if extra:
            heading = _first_heading(extra)
            lines.append(f":page_facing_up: Artifact: `{extra}`")
            if heading:
                lines.append(f"  _{heading}_")
    elif event == "blocked":
        lines.append(":stop_sign: *Blocked* — human intervention needed.")
        if extra:
            lines.append(extra)
    elif extra:
        lines.append(extra)

    payload = json.dumps({"text": "\n".join(lines)}).encode()

    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def notify_from_spec(spec: dict, phase: str, event: str, extra: str = "") -> None:
    webhook = spec.get("slack_webhook", "")
    if webhook:
        send(webhook, phase, event, spec.get("project_name", ""), extra)

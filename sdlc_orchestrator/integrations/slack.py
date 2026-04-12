"""Slack notifications via incoming webhook."""
from __future__ import annotations

import json
import urllib.request
from typing import Optional


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
        lines.append(f":bust_in_silhouette: *Human approval required*")
        lines.append(f"Run `sdlc approve` or answer the questions in the project directory.")
    elif event == "blocked":
        lines.append(f":stop_sign: *Blocked* — human intervention needed.")

    if extra:
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

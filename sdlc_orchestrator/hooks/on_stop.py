"""
Hook: fires when Claude Code stops (end of session).

Registered in .claude/settings.json as:
  { "hooks": { "Stop": [{ "matcher": "", "command": "python -m sdlc_orchestrator.hooks.on_stop" }] } }
"""
from __future__ import annotations
import os
from pathlib import Path


def main() -> None:
    project_dir = Path.cwd()
    workflow_dir = project_dir / "workflow"
    workflow_dir.mkdir(exist_ok=True)

    signal_file = workflow_dir / "state_signal.txt"
    if not signal_file.exists():
        signal_file.write_text("stopped\n")


if __name__ == "__main__":
    main()

"""
Hook: fires when Claude finishes a turn (Stop event).

Checks if Claude wrote a PHASE_COMPLETE or PHASE_BLOCKED signal to
workflow/state_signal.txt and advances the state machine accordingly.

Registered in .claude/settings.json as:
  { "hooks": { "Stop": [{ "command": "python -m sdlc_orchestrator.hooks.on_stop" }] } }
"""
from __future__ import annotations
import sys
from pathlib import Path


def main() -> None:
    project_dir = Path.cwd()
    signal_file = project_dir / "workflow" / "state_signal.txt"

    if not signal_file.exists():
        sys.exit(0)

    signal = signal_file.read_text().strip()
    signal_file.unlink(missing_ok=True)

    if signal.startswith("PHASE_COMPLETE"):
        # Trigger orchestrator to advance state
        import subprocess
        subprocess.run(
            ["python", "-m", "sdlc_orchestrator.runner", "--advance"],
            cwd=str(project_dir),
        )
    elif signal.startswith("PHASE_BLOCKED"):
        reason = signal.removeprefix("PHASE_BLOCKED:").strip()
        import subprocess
        subprocess.run(
            ["python", "-m", "sdlc_orchestrator.runner", "--block", reason],
            cwd=str(project_dir),
        )


if __name__ == "__main__":
    main()

"""
Hook: fires after every Bash tool call Claude makes.

Reads the exit code and command from env vars set by Claude Code hooks:
  CLAUDE_TOOL_INPUT  — JSON of the tool input
  CLAUDE_TOOL_OUTPUT — JSON of the tool result

Detects test failures and writes to workflow/state_signal.txt if needed.

Registered in .claude/settings.json as:
  { "hooks": { "PostToolUse": [{ "matcher": "Bash", "command": "python -m sdlc_orchestrator.hooks.on_bash" }] } }
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

TEST_FAILURE_PATTERNS = [
    "FAIL", "ERROR", "test failed", "AssertionError",
    "FAILED", "Tests failed", "npm test", "pytest",
]


def main() -> None:
    tool_output_raw = os.environ.get("CLAUDE_TOOL_OUTPUT", "{}")
    try:
        tool_output = json.loads(tool_output_raw)
    except json.JSONDecodeError:
        tool_output = {}

    output_text = str(tool_output.get("output", "") or tool_output.get("stdout", ""))
    exit_code = int(tool_output.get("exit_code", 0) or 0)

    project_dir = Path.cwd()
    workflow_dir = project_dir / "workflow"
    workflow_dir.mkdir(exist_ok=True)

    # Detect test failures
    if exit_code != 0:
        lower = output_text.lower()
        if any(p.lower() in lower for p in TEST_FAILURE_PATTERNS):
            # Log failure for the orchestrator
            log = workflow_dir / "test_failure.log"
            log.write_text(output_text[-2000:])  # last 2k chars


if __name__ == "__main__":
    main()

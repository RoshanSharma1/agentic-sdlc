"""Shared helpers used across CLI command modules."""
from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from sdlc_orchestrator.state_machine import Phase, WorkflowState
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.utils import find_project_dir

console = Console()


def require_project() -> Path:
    d = find_project_dir()
    if not d:
        console.print("[red]No SDLC project found. Run [bold]sdlc init .[/bold] first.[/red]")
        sys.exit(1)
    return d


def make_workflow_state(project_dir: Path) -> WorkflowState:
    """Return a WorkflowState with the Slack notifier pre-wired from spec.yaml."""
    from sdlc_orchestrator.integrations.slack import notify_from_spec

    spec = MemoryManager(project_dir).spec()

    def _notify(new_phase: Phase, artifact_path: str) -> None:
        notify_from_spec(spec, new_phase.value, "awaiting_approval", extra=artifact_path)

    return WorkflowState(project_dir, notifier=_notify)

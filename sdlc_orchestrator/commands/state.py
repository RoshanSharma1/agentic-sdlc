from __future__ import annotations

import sys

import click

from sdlc_orchestrator.state_machine import (
    APPROVAL_STATES, STATE_LABELS, TRANSITIONS, State, WorkflowState,
)
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.commands import console, make_workflow_state, require_project


@click.group()
def state():
    """Read and update workflow state (used by Claude during /sdlc-orchestrate)."""
    pass


@state.command("get")
def state_get():
    """Print current state — machine-readable output for Claude."""
    project_dir = require_project()
    wf = WorkflowState(project_dir)
    spec = MemoryManager(project_dir).spec()

    artifacts = {k: v for k, v in wf.artifacts.items() if v}
    allowed_next = [s.value for s in TRANSITIONS.get(wf.state, [])]

    click.echo(f"state: {wf.state.value}")
    click.echo(f"label: {STATE_LABELS.get(wf.state, '')}")
    click.echo(f"approval_needed: {wf.approval_needed}")
    click.echo(f"retry_count: {wf.retry_count}")
    click.echo(f"project: {spec.get('project_name', project_dir.name)}")
    click.echo(f"branch: {wf._data.get('current_branch', 'main')}")
    click.echo(f"allowed_transitions: {', '.join(allowed_next)}")
    if wf.current_story:
        click.echo(f"current_story: {wf.current_story}")
    if wf.completed_stories:
        click.echo(f"completed_stories: {', '.join(wf.completed_stories)}")
    story_ids = sorted(wf.github_story_items.keys())
    pending = [s for s in story_ids if s not in wf.completed_stories]
    if pending:
        click.echo(f"pending_stories: {', '.join(pending)}")
    if wf.blocked_reason:
        click.echo(f"blocked_reason: {wf.blocked_reason}")
    if artifacts:
        click.echo(f"artifacts: {', '.join(artifacts)}")


@state.command("set")
@click.argument("new_state")
@click.option("--force", is_flag=True, help="Skip transition validation")
def state_set(new_state, force):
    """Transition to a new state. Called by Claude after completing a phase."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)
    try:
        target = State(new_state)
    except ValueError:
        valid = [s.value for s in State]
        click.echo(f"Unknown state: {new_state}\nValid states:\n" + "\n".join(f"  {s}" for s in valid), err=True)
        sys.exit(1)

    if force:
        wf._data["state"] = target.value
        wf._data["approval_needed"] = target in APPROVAL_STATES
        wf.save()
    else:
        try:
            wf.transition(target)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"ok: {wf.state.value}")


@state.command("approve")
def state_approve():
    """Advance past the current approval gate. Fallback for projects without GitHub."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)

    if wf.state not in APPROVAL_STATES:
        click.echo(f"Not an approval gate: {wf.state.value}", err=True)
        sys.exit(1)

    NEXT: dict[State, State] = {
        State.REQUIREMENT_READY_FOR_APPROVAL: State.DESIGN_IN_PROGRESS,
        State.AWAITING_DESIGN_APPROVAL:       State.TASK_PLAN_IN_PROGRESS,
        State.TASK_PLAN_READY:                State.STORY_IN_PROGRESS,
        State.STORY_AWAITING_REVIEW:          State.STORY_IN_PROGRESS,
        State.BLOCKED:                        State.REQUIREMENT_IN_PROGRESS,
    }
    next_state = NEXT[wf.state]
    wf.transition(next_state)
    click.echo(f"approved: {next_state.value}")
    console.print(f"[green]✓ Approved.[/green] → {STATE_LABELS.get(next_state, next_state.value)}")
    console.print("  Tell Claude to continue (/sdlc-orchestrate).")


@state.command("history")
def state_history():
    """Print state transition history."""
    project_dir = require_project()
    wf = WorkflowState(project_dir)
    for h in wf._data.get("history", []):
        click.echo(f"{h['timestamp'][:19]}  {h['state']}")

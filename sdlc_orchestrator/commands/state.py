from __future__ import annotations

import sys

import click

from sdlc_orchestrator.state_machine import (
    Phase, Status, StoryStatus, WorkflowState, _LEGACY_MAP,
    PHASE_LABELS, STATUS_LABELS,
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

    click.echo(f"phase: {wf.phase.value}")
    click.echo(f"status: {wf.status.value}")
    click.echo(f"label: {wf.label()}")
    click.echo(f"approval_needed: {wf.is_approval_gate()}")
    click.echo(f"retry_count: {wf.retry_count}")
    click.echo(f"project: {spec.get('project_name', project_dir.name)}")
    click.echo(f"branch: {wf._data.get('current_branch', 'main')}")
    click.echo(f"base_branch: {wf._data.get('base_branch', 'main')}")

    phase_approvals = spec.get("phase_approvals", {})
    if phase_approvals:
        bypassed = [p for p, v in phase_approvals.items() if not v]
        if bypassed:
            click.echo(f"bypass_approvals: {', '.join(bypassed)}")

    if wf.phase == Phase.IMPLEMENTATION:
        if wf.current_story:
            click.echo(f"current_story: {wf.current_story}")
            if wf.story_status:
                click.echo(f"story_status: {wf.story_status.value}")
            if wf.current_task:
                click.echo(f"current_task: {wf.current_task}")
        done = wf.completed_stories
        if done:
            click.echo(f"completed_stories: {', '.join(done)}")
        all_stories = sorted(wf.github_story_items.keys())
        pending = [s for s in all_stories if s not in done]
        if pending:
            click.echo(f"pending_stories: {', '.join(pending)}")

    if wf.blocked_reason:
        click.echo(f"blocked_reason: {wf.blocked_reason}")
    for key, path in artifacts.items():
        click.echo(f"artifact_{key}: {path}")


@state.command("set")
@click.argument("value")
@click.option("--force", is_flag=True, help="Skip validation")
def state_set(value: str, force: bool):
    """Set phase/status. Accepts phase name, status name, or legacy state string."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)

    # Blocked shorthand
    if value == "blocked":
        wf.set_blocked("manually set via CLI")
        click.echo("ok: blocked")
        return

    # Legacy flat state → new phase/status
    if value in _LEGACY_MAP:
        phase, status = _LEGACY_MAP[value]
        if phase == Phase.DONE or value == "done":
            wf._data["phase"] = Phase.DONE.value
            wf._data["status"] = Status.DONE.value
            wf.save()
            click.echo("ok: done")
            return
        if phase:
            wf._data["phase"] = phase.value
        wf._data["status"] = status.value
        # keep phases dict in sync
        if phase and phase != Phase.DONE:
            wf._data["phases"][phase.value]["status"] = status.value
        wf.save()
        click.echo(f"ok: {wf.phase.value}:{wf.status.value}")
        return

    # New phase name (e.g. "design", "implementation")
    try:
        phase = Phase(value)
        wf._data["phase"] = phase.value
        wf._data["status"] = Status.IN_PROGRESS.value
        if phase != Phase.DONE:
            wf._data["phases"][phase.value]["status"] = Status.IN_PROGRESS.value
        wf.save()
        click.echo(f"ok: {phase.value}:in_progress")
        return
    except ValueError:
        pass

    # New status name (e.g. "awaiting_approval", "in_progress")
    try:
        status = Status(value)
        if status == Status.AWAITING_APPROVAL:
            wf.submit_for_approval()
        elif status == Status.IN_PROGRESS:
            wf.unblock() if wf.status == Status.BLOCKED else None
            wf._data["status"] = Status.IN_PROGRESS.value
            wf.save()
        elif status == Status.DONE:
            wf._data["phase"] = Phase.DONE.value
            wf._data["status"] = Status.DONE.value
            wf.save()
        else:
            wf._data["status"] = status.value
            wf.save()
        click.echo(f"ok: {wf.phase.value}:{wf.status.value}")
        return
    except ValueError:
        pass

    valid_phases = [p.value for p in Phase]
    valid_statuses = [s.value for s in Status]
    valid_legacy = sorted(_LEGACY_MAP.keys())
    click.echo(
        f"Unknown value: {value}\n"
        f"Phases: {', '.join(valid_phases)}\n"
        f"Statuses: {', '.join(valid_statuses)}\n"
        f"Legacy: {', '.join(valid_legacy)}",
        err=True,
    )
    sys.exit(1)


@state.command("approve")
def state_approve():
    """Advance past the current approval gate."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)

    if not wf.is_approval_gate():
        click.echo(f"Not an approval gate: {wf.phase.value}:{wf.status.value}", err=True)
        sys.exit(1)

    wf.approve()
    click.echo(f"approved: {wf.phase.value}:{wf.status.value}")
    console.print(f"[green]✓ Approved.[/green] → {wf.label()}")
    console.print("  Tell Claude to continue (/sdlc-orchestrate).")


@state.command("history")
def state_history():
    """Print state transition history."""
    project_dir = require_project()
    wf = WorkflowState(project_dir)
    for h in wf._data.get("history", []):
        ts = h.get("timestamp", "")[:19]
        phase = h.get("phase", "?")
        status = h.get("status", "?")
        story = f" [{h['story']}]" if h.get("story") else ""
        click.echo(f"{ts}  {phase}:{status}{story}")


def _set_phase_approvals(value: bool) -> None:
    project_dir = require_project()
    mem = MemoryManager(project_dir)
    if not mem.spec():
        click.echo("No spec.yaml found.", err=True)
        sys.exit(1)
    mem.set_phase_approvals(value)
    click.echo(f"ok: phase approvals {'disabled' if not value else 'enabled'} for all phases")


@state.command("no-approvals")
def state_no_approvals():
    """Disable all phase approval gates — agent advances automatically."""
    _set_phase_approvals(False)


@state.command("approvals")
def state_approvals():
    """Re-enable all phase approval gates (default behaviour)."""
    _set_phase_approvals(True)


@state.command("backfill-plan")
@click.option("--dry-run", is_flag=True, help="Print what would be written without saving")
@click.option("--plan", "plan_override", default=None, type=click.Path(exists=True), help="Path to plan.md (overrides artifacts.plan)")
def state_backfill_plan(dry_run: bool, plan_override: str | None):
    """Backfill implementation stories and tasks from the plan.md artifact."""
    import re
    from pathlib import Path as _Path

    project_dir = require_project()
    wf = WorkflowState(project_dir)

    if plan_override:
        plan_file = _Path(plan_override).resolve()
    else:
        plan_path = wf.artifacts.get("plan")
        if not plan_path:
            click.echo("error: no plan artifact in state (use --plan to specify)", err=True)
            sys.exit(1)
        plan_file = project_dir / plan_path
        if not plan_file.exists():
            click.echo(f"error: plan file not found: {plan_file}", err=True)
            sys.exit(1)

    text = plan_file.read_text()

    # Parse stories and tasks
    stories: dict = {}
    current_story: str | None = None

    for line in text.splitlines():
        # Story heading: "# STORY-001: Title" or "## Story 001 — Title" or "## Story 001 - Title"
        story_match = (
            re.match(r'^#+\s+(STORY-\w+)[:\s]+(.*)', line) or
            re.match(r'^#+\s+Story\s+(\d+)\s*[—\-]+\s*(.*)', line)
        )
        # Task heading: "## TASK-001: Title" or "- [x] `TASK-001` Title" or "- [ ] TASK-001: Title"
        task_match = (
            re.match(r'^#+\s+(TASK-\w+)[:\s]+(.*)', line) or
            re.match(r'^-\s+\[.\]\s+`(TASK-\w+)`\s+(.*)', line) or
            re.match(r'^-\s+\[.\]\s+(TASK-\w+)[:\s]+(.*)', line)
        )
        status_match = re.match(r'-\s+Status:\s+\[(.)\]', line.strip())
        # Inline status from task list bullet: "- [x] `TASK-001`" or "- [ ] TASK-001"
        inline_done_match = re.match(r'^-\s+\[(.)\]\s+(?:`?)TASK-', line)

        if story_match:
            sid_raw = story_match.group(1).strip()
            # Normalise numeric-only to STORY-NNN
            sid = sid_raw if sid_raw.startswith('STORY') else f"STORY-{sid_raw.zfill(3)}"
            current_story = sid
            if current_story not in stories:
                stories[current_story] = {
                    "name": story_match.group(2).strip(),
                    "tasks": {},
                }

        elif task_match and current_story:
            task_id = task_match.group(1)
            inline_done = inline_done_match.group(1).lower() == 'x' if inline_done_match else False
            stories[current_story]["tasks"][task_id] = {
                "name": task_match.group(2).strip(),
                "status": "done" if inline_done else "pending",
            }
            stories[current_story]["_last_task"] = task_id

        elif status_match and current_story:
            done = status_match.group(1).lower() == 'x'
            last_task = stories[current_story].get("_last_task")
            if last_task:
                stories[current_story]["tasks"][last_task]["status"] = "done" if done else "pending"

    # Infer story status from tasks
    existing = wf._data.get("phases", {}).get("implementation", {}).get("stories", {})
    merged: dict = {}
    for sid, sdata in stories.items():
        tasks = sdata["tasks"]
        done_count = sum(1 for t in tasks.values() if t["status"] == "done")
        if done_count == len(tasks) and tasks:
            story_status = "done"
        elif done_count > 0:
            story_status = "in_progress"
        else:
            story_status = "pending"

        prev = existing.get(sid, {})
        merged[sid] = {
            "name": sdata.get("name") or prev.get("name"),
            "status": prev.get("status", story_status),
            "current_task": prev.get("current_task"),
            "github_issue": prev.get("github_issue"),
            "github_pr": prev.get("github_pr"),
            "tasks": {
                tid: {
                    **prev.get("tasks", {}).get(tid, {}),
                    "name": tdata.get("name"),
                    "status": tdata["status"],
                }
                for tid, tdata in tasks.items()
            },
        }
        # clean up None values
        merged[sid] = {k: v for k, v in merged[sid].items() if v is not None}
        merged[sid]["tasks"] = merged[sid].get("tasks", {})

    if dry_run:
        import json
        click.echo(json.dumps(merged, indent=2))
        return

    wf._data.setdefault("phases", {}).setdefault("implementation", {})["stories"] = merged
    wf.save()

    story_count = len(merged)
    task_count = sum(len(s["tasks"]) for s in merged.values())
    click.echo(f"backfilled {story_count} stories, {task_count} tasks from {plan_file.name}")

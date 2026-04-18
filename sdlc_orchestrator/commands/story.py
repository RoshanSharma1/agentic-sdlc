from __future__ import annotations

import sys

import click

from sdlc_orchestrator.state_machine import State
from sdlc_orchestrator.commands import make_workflow_state, require_project


@click.group()
def story():
    """Manage per-story progress during the implementation phase."""
    pass


@story.command("start")
@click.argument("story_id")
def story_start(story_id):
    """Set the active story and transition to story_in_progress."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)
    wf.set_current_story(story_id)
    if wf.state != State.STORY_IN_PROGRESS:
        try:
            wf.transition(State.STORY_IN_PROGRESS)
        except ValueError:
            wf._data["state"] = State.STORY_IN_PROGRESS.value
            wf._data["approval_needed"] = False
            wf.save()
    click.echo(f"started: {story_id}")


@story.command("complete")
def story_complete():
    """Mark the current story as approved and advance to the next story or done."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)
    if not wf.current_story:
        click.echo("error: no current_story set", err=True)
        sys.exit(1)

    story_id = wf.current_story
    wf.complete_current_story()

    # Mark all tasks for this story as done in the plan file
    _mark_story_tasks_done(project_dir, story_id)

    all_stories = sorted(wf.github_story_items.keys())
    pending = [s for s in all_stories if s not in wf.completed_stories]

    if pending:
        click.echo(f"next: {pending[0]}")
    else:
        click.echo("all_complete")


def _mark_story_tasks_done(project_dir, story_id: str) -> None:
    """Update plan file to mark all tasks under story_id as done."""
    import re
    from pathlib import Path
    from sdlc_orchestrator.utils import project_slug

    slug = project_slug(project_dir)
    candidates = [
        project_dir / f"docs/sdlc-{slug}-plan.md",
        project_dir.parent / f"docs/sdlc-{slug}-plan.md",
        project_dir.parent.parent / f"docs/sdlc-{slug}-plan.md",
    ]
    plan_path = next((p for p in candidates if p.exists()), None)
    if not plan_path:
        return

    lines = plan_path.read_text().splitlines()
    in_story = False
    updated = []
    for line in lines:
        if re.match(rf"^#{{1,2}}\s+{re.escape(story_id)}\b", line):
            in_story = True
        elif re.match(r"^#{1,2}\s+STORY-\d+", line):
            in_story = False
        if in_story:
            # Mark Status: [ ] as Status: [x]
            line = re.sub(r"(Status:\s*)\[ \]", r"\1[x]", line, flags=re.I)
            # Mark checkbox tasks: - [ ] TASK
            line = re.sub(r"^(\s*[-*]\s+)\[ \]", r"\1[x]", line)
        updated.append(line)
    plan_path.write_text("\n".join(updated) + "\n")

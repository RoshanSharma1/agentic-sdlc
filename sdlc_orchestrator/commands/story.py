from __future__ import annotations

import sys

import click

from sdlc_orchestrator.state_machine import Phase, StoryStatus, WorkflowState
from sdlc_orchestrator.commands import make_workflow_state, require_project


@click.group()
def story():
    """Manage per-story progress during the implementation phase."""
    pass


@story.command("start")
@click.argument("story_id")
def story_start(story_id: str):
    """Begin work on a story (transitions to implementation:in_progress)."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)
    wf.start_story(story_id)
    click.echo(f"started: {story_id}")


@story.command("complete")
def story_complete():
    """Mark the current story approved; print next story id or 'all_complete'."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)
    if not wf.current_story:
        click.echo("error: no current_story set", err=True)
        sys.exit(1)

    story_id = wf.current_story
    next_story = wf.complete_story()

    _mark_story_tasks_done(project_dir, story_id)

    if next_story:
        click.echo(f"next: {next_story}")
    else:
        click.echo("all_complete")


@story.command("task-start")
@click.argument("story_id")
@click.argument("task_id")
def task_start(story_id: str, task_id: str):
    """Mark a task as in_progress within a story."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)
    wf.update_task(story_id, task_id, "in_progress")
    click.echo(f"started: {story_id}/{task_id}")


@story.command("task-done")
@click.argument("story_id")
@click.argument("task_id")
def task_done(story_id: str, task_id: str):
    """Mark a task as done within a story."""
    project_dir = require_project()
    wf = make_workflow_state(project_dir)
    wf.update_task(story_id, task_id, "done")
    click.echo(f"done: {story_id}/{task_id}")


def _mark_story_tasks_done(project_dir, story_id: str) -> None:
    """Update plan file to mark all tasks under story_id as done."""
    import re
    from sdlc_orchestrator.utils import project_slug

    slug = project_slug(project_dir)
    plan_path = project_dir / f"docs/sdlc-{slug}-plan.md"
    if not plan_path.exists():
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
            line = re.sub(r"(Status:\s*)\[ \]", r"\1[x]", line, flags=re.I)
            line = re.sub(r"^(\s*[-*]\s+)\[ \]", r"\1[x]", line)
        updated.append(line)
    plan_path.write_text("\n".join(updated) + "\n")

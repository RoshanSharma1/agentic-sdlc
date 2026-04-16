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

    wf.complete_current_story()

    all_stories = sorted(wf.github_story_items.keys())
    pending = [s for s in all_stories if s not in wf.completed_stories]

    if pending:
        click.echo(f"next: {pending[0]}")
    else:
        click.echo("all_complete")

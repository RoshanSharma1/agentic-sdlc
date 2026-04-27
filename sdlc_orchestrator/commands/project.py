from __future__ import annotations

import sys

import click

from sdlc_orchestrator.commands import console, require_project
from sdlc_orchestrator.utils import list_projects, sdlc_home, project_slug


@click.group()
def project():
    """Manage SDLC projects."""
    pass


@project.command("list")
def project_list():
    """List all projects and their state."""
    from sdlc_orchestrator.state_machine import WorkflowState
    from sdlc_orchestrator.backend import get_runtime
    project_dir = require_project()
    projects = list_projects(project_dir)

    if not projects:
        click.echo("No projects yet. Run: /sdlc-start")
        return

    for name in projects:
        wf_state = "?"
        try:
            record = get_runtime().store.get_project(name)
            target_dir = Path(record.project_dir) if record else project_dir
            wf_state = WorkflowState(target_dir).label()
        except Exception:
            pass
        click.echo(f"  {name}  [{wf_state}]")


@project.command("close")
@click.option("--force", is_flag=True, help="Close even if state is not done.")
def project_close(force: bool):
    """Archive state.json for this project."""
    from sdlc_orchestrator.state_machine import WorkflowState

    project_dir = require_project()
    slug = project_slug(project_dir)
    home = sdlc_home(project_dir)

    wf = WorkflowState(project_dir)
    if not wf.is_done() and not force:
        click.echo(f"Project is not done. Use --force to close anyway.", err=True)
        sys.exit(1)

    state_file = home / "workflow" / "state.json"
    if state_file.exists():
        import shutil
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        archive = home / "workflow" / f"state.{ts}.archived.json"
        shutil.copy2(state_file, archive)
        console.print(f"  Archived state → {archive.name}")

    try:
        from sdlc_orchestrator.backend import get_runtime
        get_runtime().archive_project(project_dir)
    except Exception:
        pass

    console.print(f"[green]✓[/green] Project '{slug}' closed.")

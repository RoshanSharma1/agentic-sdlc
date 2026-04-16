from __future__ import annotations

import sys
from pathlib import Path

import click

from sdlc_orchestrator.commands import console, require_project
from sdlc_orchestrator.utils import (
    get_active_project, set_active_project, list_projects,
    sdlc_home, create_symlink, project_slug,
)


@click.group()
def project():
    """Manage multiple SDLC projects within one codebase."""
    pass


@project.command("list")
def project_list():
    """List all projects and show which is active."""
    project_dir = require_project()
    active = get_active_project(project_dir)
    projects = list_projects(project_dir)

    if not projects:
        click.echo("No projects yet. Run: sdlc project new <name>")
        return

    for name in projects:
        marker = "* " if name == active else "  "
        click.echo(f"{marker}{name}")


@project.command("new")
@click.argument("name")
def project_new(name: str):
    """Create a new project and switch to it."""
    project_dir = require_project()
    slug = _validate_name(name)

    home = sdlc_home(project_dir, slug)  # creates the dir
    spec_path = home / "spec.yaml"
    if spec_path.exists():
        click.echo(f"Project '{slug}' already exists. Use: sdlc project switch {slug}")
        sys.exit(1)

    # Scaffold minimal structure
    (home / "memory").mkdir(parents=True, exist_ok=True)
    (home / "workflow" / "artifacts").mkdir(parents=True, exist_ok=True)
    (home / "feedback").mkdir(parents=True, exist_ok=True)
    spec_path.write_text(f"project_name: {slug}\n")

    set_active_project(project_dir, slug)
    create_symlink(project_dir, slug)
    console.print(f"[green]✓[/green] Created project '{slug}' and set as active.")
    console.print("  Run /sdlc-setup to configure it, then /sdlc-start to begin.")


@project.command("switch")
@click.argument("name")
def project_switch(name: str):
    """Switch the active project."""
    project_dir = require_project()
    slug = _validate_name(name)

    projects = list_projects(project_dir)
    if slug not in projects:
        click.echo(f"Project '{slug}' not found. Available: {', '.join(projects) or 'none'}", err=True)
        sys.exit(1)

    set_active_project(project_dir, slug)
    console.print(f"[green]✓[/green] Switched to project '{slug}'.")


@project.command("close")
@click.option("--next", "next_project", default=None, help="Switch to this project after closing.")
def project_close(next_project: str | None):
    """
    Close the active project: sync GitHub, archive state, reset for next cycle.
    If --next is given, switches the active project after closing.
    """
    from sdlc_orchestrator.state_machine import WorkflowState
    from sdlc_orchestrator.memory import MemoryManager
    from sdlc_orchestrator.integrations import github as gh

    project_dir = require_project()
    active = get_active_project(project_dir)
    home = sdlc_home(project_dir)

    wf = WorkflowState(project_dir)
    current_state = wf.state.value

    # ── GitHub sync ───────────────────────────────────────────────────────────
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    project_info = wf.github_project

    if repo and project_info and gh.is_available():
        console.print("  Syncing GitHub...")
        for story_id, item in wf.github_story_items.items():
            item_id = item.get("item_id", "")
            if item_id:
                gh._move_item(project_info, item_id, "Done")
            issue_number = item.get("number")
            if issue_number:
                gh.close_issue(repo, issue_number,
                               comment=f"Closed — project '{active}' completed.")
        for task_id, item in wf.github_task_items.items():
            issue_number = item.get("number")
            if issue_number:
                gh.close_issue(repo, issue_number,
                               comment=f"Closed — project '{active}' completed.")
        console.print("  [green]✓[/green] GitHub issues closed, board items → Done")
    elif repo:
        console.print("  [yellow]GitHub sync skipped (gh not available or no board)[/yellow]")

    # ── Archive state ─────────────────────────────────────────────────────────
    state_file = home / "workflow" / "state.json"
    if state_file.exists():
        import shutil
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        archive = home / "workflow" / f"state.{ts}.archived.json"
        shutil.copy2(state_file, archive)
        console.print(f"  Archived state → {archive.name}")

    console.print(f"[green]✓[/green] Project '{active}' closed (was: {current_state}).")

    if next_project:
        slug = _validate_name(next_project)
        projects = list_projects(project_dir)
        if slug not in projects:
            click.echo(f"Project '{slug}' not found — staying on '{active}'.", err=True)
            return
        set_active_project(project_dir, slug)
        console.print(f"[green]✓[/green] Switched to project '{slug}'.")
    else:
        others = [p for p in list_projects(project_dir) if p != active]
        if others:
            console.print(f"  Other projects: {', '.join(others)}")
            console.print(f"  To switch: sdlc project switch <name>")


def _validate_name(name: str) -> str:
    """Normalise project name to a safe slug."""
    import re
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    if not slug:
        click.echo(f"Invalid project name: '{name}'", err=True)
        sys.exit(1)
    return slug

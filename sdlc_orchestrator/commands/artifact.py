from __future__ import annotations

import sys

import click

from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.utils import sdlc_home
from sdlc_orchestrator.commands import require_project


@click.group()
def artifact():
    """Read and list phase artifacts (used by Claude during /sdlc-orchestrate)."""
    pass


@artifact.command("read")
@click.argument("name")
def artifact_read(name):
    """Print an artifact to stdout. Claude uses this to read phase outputs."""
    project_dir = require_project()
    content = MemoryManager(project_dir).artifact(name)
    if not content:
        click.echo(f"Artifact not found: {name}", err=True)
        sys.exit(1)
    click.echo(content)


@artifact.command("list")
def artifact_list():
    """List available artifacts."""
    project_dir = require_project()
    artifacts_dir = sdlc_home(project_dir) / "workflow" / "artifacts"
    if not artifacts_dir.exists():
        click.echo("No artifacts yet.")
        return
    for f in sorted(artifacts_dir.glob("*.md")):
        click.echo(f"  {f.stem:30}  {f.stat().st_size:>6} bytes")

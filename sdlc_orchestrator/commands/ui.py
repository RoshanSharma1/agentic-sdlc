from __future__ import annotations

import webbrowser
from pathlib import Path

import click


@click.command()
@click.option("--port", default=7842, show_default=True, help="Port to listen on")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
def ui(port, no_browser):
    """Launch the SDLC dashboard."""
    try:
        import uvicorn
    except ImportError:
        click.echo("Missing dependency: pip install uvicorn fastapi", err=True)
        raise SystemExit(1)

    from sdlc_orchestrator.ui import server as srv
    from sdlc_orchestrator.utils import find_project_dir

    project_dir = find_project_dir() or Path.cwd()
    srv._project_dir = project_dir
    srv._chat_dir = project_dir

    url = f"http://localhost:{port}"
    click.echo(f"SDLC Dashboard → {url}")
    if not no_browser:
        webbrowser.open(url)

    uvicorn.run(srv.app, host="0.0.0.0", port=port, log_level="warning")

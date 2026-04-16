from __future__ import annotations

import subprocess
import sys

import click
from rich.table import Table

from sdlc_orchestrator.state_machine import APPROVAL_STATES, STATE_LABELS, State, WorkflowState
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.utils import create_symlink, project_slug, sdlc_home
from sdlc_orchestrator.commands import console, require_project

# Maps each approval gate to the branch Claude should poll
_GATE_BRANCHES: dict[str, str] = {
    "requirement_ready_for_approval": "sdlc/requirements",
    "awaiting_design_approval":       "sdlc/design",
    "task_plan_ready":                "sdlc/plan",
    "story_awaiting_review":          "",
    "blocked":                        "",
}


@click.command()
def status():
    """Show current SDLC state (human-readable)."""
    project_dir = require_project()
    wf = WorkflowState(project_dir)
    spec = MemoryManager(project_dir).spec()

    table = Table(
        title=f"SDLC — {spec.get('project_name', project_dir.name)}",
        show_header=False,
    )
    table.add_column("Key", style="dim", width=22)
    table.add_column("Value")

    color = ("yellow" if wf.state in APPROVAL_STATES
             else "green" if wf.state == State.DONE else "blue")
    table.add_row("State", f"[{color}]{wf.state.value}[/{color}]")
    table.add_row("", f"[dim]{STATE_LABELS.get(wf.state, '')}[/dim]")
    table.add_row("Approval needed", "YES — sdlc state approve" if wf.approval_needed else "no")
    table.add_row("Branch", wf._data.get("current_branch", "main"))
    table.add_row("SDLC home", str(sdlc_home(project_dir)))
    table.add_row("Last updated", wf._data.get("last_updated", "—")[:19])
    if wf.blocked_reason:
        table.add_row("Blocked", f"[red]{wf.blocked_reason}[/red]")
    console.print(table)

    history = wf._data.get("history", [])
    if history:
        console.print("\n[dim]Recent history:[/dim]")
        for h in history[-6:]:
            console.print(f"  {h['timestamp'][:19].replace('T',' ')}  {h['state']}")

    console.print("\n[dim]To continue: open Claude Code and invoke[/dim] [bold]/sdlc-orchestrate[/bold]")


@click.command()
@click.argument("phase")
@click.argument("event")
def notify(phase, event):
    """Send a Slack notification. Called by Claude at approval gates."""
    from sdlc_orchestrator.integrations.slack import notify_from_spec
    project_dir = require_project()
    spec = MemoryManager(project_dir).spec()
    notify_from_spec(spec, phase, event)
    click.echo(f"notified: {phase}/{event}")


@click.command()
@click.option("--interval", default=30, show_default=True, help="Seconds between GitHub polls")
def watch(interval):
    """Watch for PR approval and resume Claude automatically."""
    import time
    from sdlc_orchestrator.integrations.github import get_pr_status, is_available
    from sdlc_orchestrator.commands.init import trigger_agent

    project_dir = require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")

    if not repo:
        console.print("[yellow]No GitHub repo in spec.yaml — watch has nothing to poll.[/yellow]")
        sys.exit(1)

    if not is_available():
        console.print("[red]gh CLI not found or not authenticated. Run: gh auth login[/red]")
        sys.exit(1)

    console.print(f"[bold]sdlc watch[/bold] — polling GitHub every {interval}s")
    console.print(f"  repo: [dim]{repo}[/dim]")
    console.print("  Press Ctrl-C to stop.\n")

    last_state = last_pr_status = ""

    while True:
        try:
            wf = WorkflowState(project_dir)
            current_state = wf.state.value

            if wf.state == State.DONE:
                console.print("[green]✓ Workflow complete.[/green]")
                break

            branch = _GATE_BRANCHES.get(current_state, "")
            if not branch:
                if current_state != last_state:
                    console.print(f"[dim]{current_state}[/dim] — Claude is working, not at a gate")
                    last_state = current_state
                time.sleep(interval)
                continue

            pr_status = get_pr_status(repo, branch) or "not-found"
            if current_state != last_state or pr_status != last_pr_status:
                console.print(f"[dim]{current_state}[/dim]  PR [{branch}]: [bold]{pr_status}[/bold]")
                last_state, last_pr_status = current_state, pr_status

            if pr_status in ("approved", "merged"):
                console.print(f"\n[green]✓ PR approved[/green] — triggering agent to continue...")
                result = trigger_agent(project_dir)
                if result is None:
                    console.print("[yellow]Executor has no headless CLI — run /sdlc-orchestrate manually.[/yellow]")
                elif result.returncode != 0:
                    console.print("[yellow]Agent exited with an error.[/yellow]")
                last_pr_status = ""

            time.sleep(interval)

        except KeyboardInterrupt:
            console.print("\n[dim]watch stopped.[/dim]")
            break


@click.command()
@click.option("--port", default=8080, show_default=True)
@click.option("--secret", default="", envvar="SDLC_WEBHOOK_SECRET",
              help="GitHub webhook secret (or set SDLC_WEBHOOK_SECRET env var)")
def webhook(port, secret):
    """Start a GitHub webhook receiver for real-time PR event triggers."""
    import hashlib
    import hmac
    import json as _json
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from sdlc_orchestrator.commands.init import trigger_agent

    project_dir = require_project()

    class WebhookHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_POST(self):
            if self.path != "/webhook":
                self.send_response(404); self.end_headers(); return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            if secret:
                sig_header = self.headers.get("X-Hub-Signature-256", "")
                expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig_header, expected):
                    self.send_response(401); self.end_headers(); return

            try:
                payload = _json.loads(body)
            except _json.JSONDecodeError:
                self.send_response(400); self.end_headers(); return

            event = self.headers.get("X-GitHub-Event", "")
            triggered = False

            if event == "pull_request":
                action = payload.get("action")
                merged = payload.get("pull_request", {}).get("merged", False)
                branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
                if action == "closed" and merged and branch.startswith("sdlc/"):
                    console.print(f"[green]✓ PR merged:[/green] {branch} — triggering Claude")
                    triggered = True

            elif event == "pull_request_review":
                state = payload.get("review", {}).get("state", "").upper()
                branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
                if state == "APPROVED" and branch.startswith("sdlc/"):
                    console.print(f"[green]✓ PR approved:[/green] {branch} — triggering Claude")
                    triggered = True

            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

            if triggered:
                result = trigger_agent(project_dir)
                if result is None:
                    console.print("[yellow]Executor has no headless CLI — run /sdlc-orchestrate manually.[/yellow]")

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            else:
                self.send_response(404); self.end_headers()

    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    console.print(f"[bold]sdlc webhook[/bold] — listening on port {port}")
    console.print(f"  Set GitHub webhook URL to: http://<your-host>:{port}/webhook")
    if secret:
        console.print("  Signature verification: [green]enabled[/green]")
    else:
        console.print("  Signature verification: [yellow]disabled[/yellow]")
    console.print("  Press Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]webhook server stopped.[/dim]")


@click.group()
def tick():
    """Tick-lock helpers — prevent concurrent /sdlc-orchestrate runs."""
    pass


@tick.command("acquire")
def tick_acquire():
    """Acquire the tick lock. Exits non-zero if already locked."""
    import fcntl
    import os
    project_dir = require_project()
    lock_path = sdlc_home(project_dir) / "workflow" / "tick.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fh.flush()
        lock_path.with_suffix(".pid").write_text(str(os.getpid()))
        click.echo("ok: lock acquired")
    except BlockingIOError:
        click.echo("locked: another tick is running", err=True)
        sys.exit(1)


@tick.command("release")
def tick_release():
    """Release the tick lock."""
    project_dir = require_project()
    lock_path = sdlc_home(project_dir) / "workflow" / "tick.lock"
    for p in (lock_path, lock_path.with_suffix(".pid")):
        p.unlink(missing_ok=True)
    click.echo("ok: lock released")


@click.command()
@click.option("--all", "relink_all", is_flag=True)
def relink(relink_all):
    """Rebuild ~/.sdlc/projects/<slug> symlink."""
    from pathlib import Path
    if relink_all:
        projects_dir = Path.home() / ".sdlc" / "projects"
        if not projects_dir.exists():
            console.print("[yellow]No ~/.sdlc/projects/ found.[/yellow]")
            return
        for link in projects_dir.iterdir():
            status_str = "[green]ok[/green]" if (link.is_symlink() and link.exists()) else "[red]broken[/red]"
            target = link.readlink() if link.is_symlink() else "?"
            console.print(f"  {status_str}  {link.name} → {target}")
        return

    project_dir = require_project()
    link = create_symlink(project_dir)
    console.print(f"[green]✓[/green] ~/.sdlc/projects/{project_slug(project_dir)} → {link.resolve()}")

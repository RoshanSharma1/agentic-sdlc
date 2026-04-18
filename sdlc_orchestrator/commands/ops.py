from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click
from rich.table import Table

from sdlc_orchestrator.state_machine import APPROVAL_STATES, STATE_LABELS, State, WorkflowState
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.utils import create_symlink, project_slug, sdlc_home
from sdlc_orchestrator.commands import console, require_project

# Maps each approval gate to the branch Claude should poll
# Branch names are project-namespaced: sdlc-$PROJECT-<phase>
# The actual branch name is resolved at runtime using the active project name.
_GATE_BRANCH_SUFFIXES: dict[str, str] = {
    "requirement_ready_for_approval": "requirements",
    "awaiting_design_approval":       "design",
    "task_plan_ready":                "plan",
    "story_awaiting_review":          "",
    "documentation_awaiting_approval": "docs",
    "blocked":                        "",
}


@click.command()
def status():
    """Show current SDLC state (human-readable)."""
    project_dir = require_project()

    # Show all active projects from .sdlc/active
    active_file = project_dir / ".sdlc" / "active"
    if active_file.exists():
        projects = [p for p in active_file.read_text().splitlines() if p.strip()]
        if len(projects) > 1:
            console.print("[bold]Active projects:[/bold]")
            for p in projects:
                wf_state = "?"
                try:
                    wf_state = WorkflowState(project_dir / "worktree" / p).state.value
                except Exception:
                    pass
                console.print(f"  {p}  [dim]{wf_state}[/dim]")
            console.print()

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


def _find_all_project_dirs(scan_dir: Path | None = None) -> list[tuple[Path, str]]:
    """Return (project_dir, project_name) for all projects found.
    Checks ~/.sdlc/projects symlinks first, then scans scan_dir for .sdlc/ directories."""
    from sdlc_orchestrator.utils import get_active_project
    results = []
    seen = set()

    # 1. ~/.sdlc/projects symlinks
    projects_home = Path.home() / ".sdlc" / "projects"
    if projects_home.exists():
        for link in projects_home.iterdir():
            if not (link.is_symlink() and link.exists()):
                continue
            try:
                sdlc_projects_dir = link.resolve().parent
                project_dir = sdlc_projects_dir.parent.parent
                if (project_dir / ".sdlc").exists() and project_dir not in seen:
                    seen.add(project_dir)
                    results.append((project_dir, link.resolve().name))
            except Exception:
                continue

    # 2. Scan directory for .sdlc/ subdirectories
    if scan_dir and scan_dir.exists():
        for child in scan_dir.iterdir():
            if child.is_dir() and (child / ".sdlc").exists() and child not in seen:
                seen.add(child)
                active = get_active_project(child)
                results.append((child, active))

    return results


@click.command()
@click.option("--interval", default=30, show_default=True, help="Seconds between polls")
@click.option("--stale-timeout", default=600, show_default=True,
              help="Seconds before a non-advancing project is considered stuck")
@click.option("--all-projects", is_flag=True, default=True, show_default=True,
              help="Watch all projects under ~/.sdlc/projects (default: on)")
@click.option("--projects-dir", default=None, type=click.Path(exists=True, file_okay=False),
              help="Directory to scan for projects (defaults to parent of cwd)")
def watch(interval, stale_timeout, all_projects, projects_dir):
    """Watch all projects for PR approvals and stuck agents, trigger automatically."""
    import os
    import time
    from sdlc_orchestrator.integrations.github import get_pr_status, is_available
    from sdlc_orchestrator.integrations.slack import notify_from_spec
    from sdlc_orchestrator.commands.init import trigger_agent

    # Collect projects to watch
    scan = Path(projects_dir) if projects_dir else Path.cwd().parent
    if all_projects:
        project_entries = _find_all_project_dirs(scan_dir=scan)
        if not project_entries:
            project_dir = require_project()
            project_entries = [(project_dir, None)]
    else:
        project_dir = require_project()
        project_entries = [(project_dir, None)]

    console.print(f"[bold]sdlc watch[/bold] — polling every {interval}s, stale timeout {stale_timeout}s")
    console.print(f"  watching {len(project_entries)} project(s)")
    console.print("  Press Ctrl-C to stop.\n")

    # Per-project tracking: {project_dir: {state, state_since, last_pr_status}}
    tracking: dict[Path, dict] = {}

    while True:
        try:
            now = time.time()

            for project_dir, project_name in project_entries:
                try:
                    wf = WorkflowState(project_dir)
                except Exception:
                    continue

                current_state = wf.state.value
                spec = MemoryManager(project_dir).spec()
                repo = spec.get("repo", "")
                label = spec.get("project_name", project_dir.name)

                track = tracking.setdefault(project_dir, {
                    "state": current_state,
                    "state_since": now,
                    "last_pr_status": "",
                })

                # Detect state change — reset stale timer
                if track["state"] != current_state:
                    track["state"] = current_state
                    track["state_since"] = now
                    track["last_pr_status"] = ""

                if wf.state == State.DONE:
                    continue

                # ── blocked: notify Slack, don't trigger ──────────────────
                if current_state == "blocked":
                    stale_secs = now - track["state_since"]
                    if stale_secs < interval * 2:  # only notify once
                        console.print(f"[red]BLOCKED[/red]  {label}: {wf.blocked_reason or 'no reason given'}")
                        if repo:
                            try:
                                notify_from_spec(spec, "blocked", "blocked")
                            except Exception:
                                pass
                    continue

                # ── stale: state hasn't changed in stale_timeout ──────────
                stale_secs = now - track["state_since"]
                if stale_secs >= stale_timeout:
                    console.print(f"[yellow]STUCK[/yellow]    {label}: '{current_state}' for {int(stale_secs)}s — triggering agent")
                    result = trigger_agent(project_dir)
                    if result is None:
                        console.print(f"  [yellow]{label}: executor has no headless CLI[/yellow]")
                    track["state_since"] = now  # reset to avoid re-triggering immediately
                    continue

                # ── approval gate: poll PR ────────────────────────────────
                if not repo or not is_available():
                    continue

                slug = project_slug(project_dir)
                active = project_name or wf._data.get("active_project", slug)
                suffix = _GATE_BRANCH_SUFFIXES.get(current_state, "")
                if current_state == "story_awaiting_review":
                    branch = f"sdlc-{active}-{wf.current_story.lower()}" if wf.current_story else ""
                elif suffix:
                    branch = f"sdlc-{active}-{suffix}"
                else:
                    branch = ""

                if not branch:
                    continue

                pr_status = get_pr_status(repo, branch) or "not-found"
                if pr_status != track["last_pr_status"]:
                    console.print(f"[dim]{label}[/dim]  {current_state}  PR [{branch}]: [bold]{pr_status}[/bold]")
                    track["last_pr_status"] = pr_status

                if pr_status in ("approved", "merged"):
                    console.print(f"[green]✓ PR approved[/green]  {label} — triggering agent")
                    result = trigger_agent(project_dir)
                    if result is None:
                        console.print(f"  [yellow]{label}: executor has no headless CLI[/yellow]")
                    track["last_pr_status"] = ""

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
    """Acquire the tick lock. Exits non-zero if already locked or state is done."""
    import fcntl
    import os
    project_dir = require_project()

    wf = WorkflowState(project_dir)
    if wf.state == State.DONE:
        click.echo("done: workflow is complete — run 'sdlc project close' to start a new cycle", err=True)
        sys.exit(2)

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

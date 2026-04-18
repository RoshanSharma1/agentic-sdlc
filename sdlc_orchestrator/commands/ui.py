from __future__ import annotations

import subprocess
import threading
import time
import webbrowser
from pathlib import Path

import click


def _detect_ngrok_url(port: int) -> tuple[str | None, int]:
    """Returns (public_url, actual_port). actual_port may differ from requested if reusing existing tunnel."""
    try:
        import httpx
        for host in ["host.docker.internal", "127.0.0.1"]:
            try:
                resp = httpx.get(f"http://{host}:4040/api/tunnels", timeout=3)
                tunnels = resp.json().get("tunnels", [])
                # Exact match first
                for t in tunnels:
                    pub = t.get("public_url", "")
                    addr = t.get("config", {}).get("addr", "")
                    if pub.startswith("https://") and str(port) in addr:
                        return pub, port
                # Reuse existing tunnel — extract its port and use that
                for t in tunnels:
                    pub = t.get("public_url", "")
                    addr = t.get("config", {}).get("addr", "")
                    if pub.startswith("https://") and addr:
                        import re
                        m = re.search(r":(\d+)$", addr)
                        existing_port = int(m.group(1)) if m else port
                        return pub, existing_port
            except Exception:
                continue
    except ImportError:
        pass
    return None, port


def _start_watch(project_dir: Path, interval: int = 30, stale_timeout: int = 600) -> None:
    """Run sdlc watch logic in a background daemon thread."""
    from sdlc_orchestrator.commands.ops import _find_all_project_dirs, _GATE_BRANCH_SUFFIXES
    from sdlc_orchestrator.commands.init import trigger_agent
    from sdlc_orchestrator.integrations.github import get_pr_status, is_available
    from sdlc_orchestrator.state_machine import State, WorkflowState
    from sdlc_orchestrator.memory import MemoryManager
    from sdlc_orchestrator.utils import project_slug

    tracking: dict = {}

    def _loop():
        click.echo("[watch] started (30s interval)")
        while True:
            try:
                entries = _find_all_project_dirs(scan_dir=project_dir.parent) or [(project_dir, None)]
                now = time.time()
                for pd, pname in entries:
                    try:
                        wf = WorkflowState(pd)
                    except Exception:
                        continue
                    state = wf.state.value
                    spec = MemoryManager(pd).spec()
                    repo = spec.get("repo", "")
                    track = tracking.setdefault(str(pd), {"state": state, "since": now, "last_pr": ""})
                    if track["state"] != state:
                        track.update(state=state, since=now, last_pr="")
                    if wf.state == State.DONE:
                        continue
                    if now - track["since"] >= stale_timeout:
                        click.echo(f"[watch] stuck: {pname or pd.name} — re-triggering")
                        trigger_agent(pd)
                        track["since"] = now
                        continue
                    if not repo or not is_available():
                        continue
                    active = pname or project_slug(pd)
                    suffix = _GATE_BRANCH_SUFFIXES.get(state, "")
                    if state == "story_awaiting_review":
                        branch = f"sdlc-{active}-{wf.current_story.lower()}" if wf.current_story else ""
                    elif suffix:
                        branch = f"sdlc-{active}-{suffix}"
                    else:
                        branch = ""
                    if not branch:
                        continue
                    pr_status = get_pr_status(repo, branch) or "not-found"
                    if pr_status != track["last_pr"]:
                        click.echo(f"[watch] {pname or pd.name}: {state} → PR {pr_status}")
                        track["last_pr"] = pr_status
                    if pr_status in ("approved", "merged"):
                        click.echo(f"[watch] ✓ approved — triggering {pname or pd.name}")
                        trigger_agent(pd)
                        track["last_pr"] = ""
            except Exception as e:
                click.echo(f"[watch] error: {e}")
            time.sleep(interval)

    threading.Thread(target=_loop, daemon=True).start()


@click.command()
@click.option("--port", default=7842, show_default=True, help="Port to listen on")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
@click.option("--ngrok", "use_ngrok", is_flag=True, help="Expose via ngrok for mobile access")
@click.option("--no-watch", is_flag=True, help="Don't start sdlc watch in background")
def ui(port, no_browser, use_ngrok, no_watch):
    """Launch dashboard + watch + ngrok in one command."""
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

    # ── ngrok ──────────────────────────────────────────────────────────────
    if use_ngrok:
        import shutil, re as _re
        # Check if a tunnel already exists before starting a new one
        existing_url, existing_port = _detect_ngrok_url(port)
        if existing_url and existing_port != port:
            click.echo(f"ngrok port {existing_port} taken — using bore tunnel → port {port} ...")
            ngrok_url = None  # force bore fallback below
        elif shutil.which("ngrok"):
            click.echo(f"Starting ngrok sdlc tunnel → port {port} ...")
            # Use named tunnel from ngrok config if available, else ad-hoc
            subprocess.Popen(
                ["ngrok", "start", "sdlc"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            ngrok_url = None
            for _ in range(15):
                time.sleep(1)
                ngrok_url, _ = _detect_ngrok_url(port)
                if ngrok_url:
                    break
            if not ngrok_url:
                # fallback: ad-hoc tunnel
                subprocess.Popen(
                    ["ngrok", "http", str(port), "--log=stdout"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                for _ in range(15):
                    time.sleep(1)
                    ngrok_url, _ = _detect_ngrok_url(port)
                    if ngrok_url:
                        break
        else:
            ngrok_url = None

        # Fallback to bore if ngrok failed or port was taken
        if not ngrok_url and shutil.which("bore"):
            click.echo(f"ngrok unavailable — using bore tunnel → port {port} ...")
            bore_proc = subprocess.Popen(
                ["bore", "local", str(port), "--to", "bore.pub"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            for _ in range(15):
                time.sleep(1)
                line = bore_proc.stdout.readline()
                m = _re.search(r"bore\.pub:(\d+)", line)
                if m:
                    ngrok_url = f"http://bore.pub:{m.group(1)}"
                    break

        if ngrok_url:
            click.echo(f"📱 Remote URL: {ngrok_url}")
            if not no_browser:
                webbrowser.open(ngrok_url)
        else:
            click.echo("WARNING: Could not create remote tunnel.")
    else:
        click.echo(f"SDLC Dashboard → http://localhost:{port}")
        if not no_browser:
            webbrowser.open(f"http://localhost:{port}")

    # ── watch ──────────────────────────────────────────────────────────────
    if not no_watch:
        _start_watch(project_dir)

    # ── server ─────────────────────────────────────────────────────────────
    uvicorn.run(srv.app, host="0.0.0.0", port=port, log_level="warning")

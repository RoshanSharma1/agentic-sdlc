"""
sdlc CLI — human tools and Claude's state/integration toolbox.

Human-facing:
  sdlc init [source]        scaffold a project (new or existing)
  sdlc answer [--file]      submit answers to requirement questions
  sdlc status               show current state in readable form
  sdlc relink [--all]       rebuild ~/.sdlc/projects/<slug> symlink

Claude-facing (called via Bash tool during /sdlc-orchestrate):
  sdlc state get            print current state (machine-readable)
  sdlc state set <state>    transition to a new state
  sdlc state approve        advance past current approval gate
  sdlc state history        show state history

  sdlc artifact read <name>         print artifact to stdout
  sdlc artifact list                list available artifacts

  sdlc notify <phase> <event>       send Slack notification
  sdlc github create-pr <branch> <phase>        open PR
  sdlc github create-issue <title> <body-file>  create issue
  sdlc github create-board                      create project board

  sdlc tick acquire         acquire tick lock (prevent concurrent runs)
  sdlc tick release         release tick lock
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from sdlc_orchestrator.state_machine import (
    State, WorkflowState, STATE_LABELS, APPROVAL_STATES, TRANSITIONS,
)
from sdlc_orchestrator.memory import MemoryManager, GLOBAL_MEMORY_PATH
from sdlc_orchestrator.utils import (
    create_symlink, find_project_dir, project_slug, sdlc_home, update_gitignore,
)

console = Console()

# ── project dir helpers ───────────────────────────────────────────────────────

def _require_project() -> Path:
    d = find_project_dir()
    if not d:
        console.print("[red]No SDLC project found. Run [bold]sdlc init[/bold] first.[/red]")
        sys.exit(1)
    return d


def _make_workflow_state(project_dir: Path) -> WorkflowState:
    """Return a WorkflowState with the Slack notifier pre-wired from spec.yaml."""
    from sdlc_orchestrator.integrations.slack import notify_from_spec

    spec = MemoryManager(project_dir).spec()

    def _notify(new_state: State, artifact_path: str) -> None:
        notify_from_spec(spec, new_state.value, "awaiting_approval",
                         extra=artifact_path)

    return WorkflowState(project_dir, notifier=_notify)

# ── source detection ──────────────────────────────────────────────────────────

def _detect_source(source: str | None) -> str:
    if not source:
        return "new"
    if source.startswith(("http://", "https://", "git@")):
        return "github"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
        return "github"
    return "local"

def _github_to_clone_url(source: str) -> str:
    return source if source.startswith(("http", "git@")) else f"https://github.com/{source}.git"

def _repo_name_from_source(source: str) -> str:
    return source.rstrip("/").split("/")[-1].removesuffix(".git")

def _detect_remote_repo(project_dir: Path) -> str:
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"],
                           cwd=project_dir, capture_output=True, text=True)
        m = re.search(r"[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", r.stdout.strip())
        return m.group(1) if m else ""
    except Exception:
        return ""

def _detect_stack(project_dir: Path) -> str:
    markers = {
        "package.json": "Node.js",
        "requirements.txt": "Python",
        "pyproject.toml": "Python",
        "go.mod": "Go",
        "Cargo.toml": "Rust",
        "pom.xml": "Java/Maven",
    }
    for f, stack in markers.items():
        if (project_dir / f).exists():
            return stack
    return ""

# ── shared setup helpers ──────────────────────────────────────────────────────

def _ensure_global_memory() -> None:
    GLOBAL_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_MEMORY_PATH.exists():
        from importlib.resources import files as _files
        try:
            content = (_files("sdlc_orchestrator") / "templates" / "global.md").read_text()
            GLOBAL_MEMORY_PATH.write_text(content)
        except Exception:
            GLOBAL_MEMORY_PATH.write_text(
                "# Global Rules\n\n- Write clean, modular code\n"
                "- Write tests for all features\n"
                "- No secrets in code\n"
            )

def _install_global_skills(force: bool = False) -> None:
    dest_dir = Path.home() / ".claude" / "commands"
    dest_dir.mkdir(parents=True, exist_ok=True)
    from importlib.resources import files as _files
    try:
        skills_pkg = _files("sdlc_orchestrator") / "skills"
        for skill_file in skills_pkg.iterdir():  # type: ignore[attr-defined]
            dest = dest_dir / skill_file.name
            if not dest.exists() or force:
                dest.write_text(skill_file.read_text(encoding="utf-8"))
                console.print(f"  [dim]skill:[/dim] ~/.claude/commands/{skill_file.name}")
    except Exception as e:
        console.print(f"  [yellow]Skill install warning: {e}[/yellow]")

def _write_hooks(project_dir: Path) -> None:
    import json
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "command": "python -m sdlc_orchestrator.hooks.on_bash", "timeout": 10}
            ],
            "Stop": [
                {"command": "python -m sdlc_orchestrator.hooks.on_stop", "timeout": 30}
            ],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))

def _init_sdlc_dirs(project_dir: Path) -> None:
    home = sdlc_home(project_dir)
    for sub in ["memory", "workflow/artifacts", "workflow/logs", "feedback"]:
        (home / sub).mkdir(parents=True, exist_ok=True)

def _set_initial_state(project_dir: Path, phase: str) -> None:
    phase_map = {
        "requirement":    State.DRAFT_REQUIREMENT,
        "design":         State.DESIGN_IN_PROGRESS,
        "planning":       State.TASK_PLAN_IN_PROGRESS,
        "implementation": State.IMPLEMENTATION_IN_PROGRESS,
        "validation":     State.TEST_FAILURE_LOOP,
        "review":         State.AWAITING_REVIEW,
    }
    wf = WorkflowState(project_dir)
    wf._data["state"] = phase_map.get(phase, State.DRAFT_REQUIREMENT).value
    wf.save()

def _detect_starting_phase(project_dir: Path) -> str:
    choices = ["requirement", "design", "planning", "implementation", "validation", "review"]
    has_src = any(
        f for f in project_dir.iterdir()
        if f.name not in {".git", ".sdlc", ".claude", "CLAUDE.md"}
    )
    if not has_src:
        return "requirement"
    console.print("\n  [bold]Which phase to start from?[/bold]")
    for i, c in enumerate(choices, 1):
        console.print(f"    {i}. {c}")
    val = click.prompt("  Number", default="1")
    try:
        return choices[int(val) - 1]
    except (ValueError, IndexError):
        return "requirement"

def _setup_github_board(project_dir: Path, spec: dict) -> None:
    from sdlc_orchestrator.integrations import github
    repo = spec.get("repo", "")
    if not repo or not github.is_available():
        return
    project_id = github.create_project_board(spec.get("project_name", ""), repo)
    if project_id:
        wf = WorkflowState(project_dir)
        wf.set_github(project_id=project_id)
        console.print(f"  [green]✓[/green] GitHub project board (id: {project_id})")
    else:
        console.print("  [yellow]GitHub board skipped (check gh auth)[/yellow]")

def _run_analyze_skill(project_dir: Path, spec: dict) -> None:
    skill_path = Path.home() / ".claude" / "commands" / "sdlc-analyze-repo.md"
    if not skill_path.exists():
        _install_global_skills()
    prompt = skill_path.read_text() if skill_path.exists() else (
        "Analyze this repo. Write .sdlc/memory/project.md covering stack, "
        "architecture, domain concepts, testing, tech debt, deployment."
    )
    prompt = prompt.replace("{{PROJECT_NAME}}", spec.get("project_name", ""))
    try:
        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions"],
            input=prompt, capture_output=True, text=True, cwd=str(project_dir), timeout=900,
        )
        if result.returncode == 0:
            console.print("  [green]✓[/green] Repo analysis complete")
        else:
            console.print("  [yellow]Repo analysis had warnings — edit .sdlc/memory/project.md[/yellow]")
    except Exception:
        console.print("  [yellow]Repo analysis skipped (claude CLI not found)[/yellow]")

def _open_in_editor(path: Path) -> None:
    import os
    editor = os.environ.get("EDITOR", "")
    if editor:
        subprocess.run([editor, str(path)])
    else:
        console.print(f"  [dim]Edit: {path}[/dim]")



def _common_setup(project_dir: Path, spec: dict, is_new: bool, upgrade_skills: bool) -> None:
    console.print()
    _ensure_global_memory()
    _init_sdlc_dirs(project_dir)

    link = create_symlink(project_dir)
    console.print(f"  [green]✓[/green] symlink ~/.sdlc/projects/{project_slug(project_dir)} → .sdlc/")

    mem = MemoryManager(project_dir)
    mem.write_spec(spec)
    console.print("  [green]✓[/green] .sdlc/spec.yaml")

    if not mem.project_path.exists():
        from importlib.resources import files as _files
        try:
            tpl = (_files("sdlc_orchestrator") / "templates" / "project.md").read_text()
            tpl = (tpl.replace("{{PROJECT_NAME}}", spec.get("project_name", ""))
                      .replace("{{STACK}}", spec.get("tech_stack", "")))
        except Exception:
            tpl = f"# Project: {spec.get('project_name', '')}\n\nStack: {spec.get('tech_stack', '')}\n"
        mem.write_project_memory(tpl)

    if not is_new:
        console.print("  Running repo analysis (Claude) ...")
        _run_analyze_skill(project_dir, spec)
        if mem.project_path.exists():
            console.print("  Review [bold].sdlc/memory/project.md[/bold] then press Enter.")
            _open_in_editor(mem.project_path)
            click.pause("  Press Enter when done ...")
            mem.regenerate_claude_md()

    mem.regenerate_claude_md()
    console.print("  [green]✓[/green] CLAUDE.md")

    WorkflowState(project_dir).save()
    console.print("  [green]✓[/green] .sdlc/workflow/state.json")

    _write_hooks(project_dir)
    console.print("  [green]✓[/green] .claude/settings.json")

    _install_global_skills(force=upgrade_skills)
    console.print("  [green]✓[/green] skills → ~/.claude/commands/  (including /sdlc-orchestrate)")

    update_gitignore(project_dir)
    console.print("  [green]✓[/green] .gitignore")

    _setup_github_board(project_dir, spec)

    console.print(f"""
[bold green]Ready.[/bold green]

  cd {project_dir}

  Next steps:
    1. Open Claude Code in this directory
    2. Run [bold]/sdlc-setup[/bold] — Claude interviews you and drafts requirements
    3. Review requirements, then run [bold]/loop 10m /sdlc-orchestrate[/bold]

  Claude drives the rest. You'll get Slack pings at each approval gate.
""")


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

@click.group()
def cli():
    """Autonomous SDLC orchestrator — Claude-driven."""
    pass


# ── sdlc setup ────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("source", required=False)
@click.option("--upgrade-skills", is_flag=True)
def init(source, upgrade_skills):
    """Scaffold a project for SDLC orchestration.

    \b
    SOURCE:
      (none)               interactive new project
      owner/repo           clone from GitHub
      https://github.com/… clone from GitHub
      .  or  /path         use existing local directory

    After running, open Claude Code and run /sdlc-setup to draft requirements.
    """
    kind = _detect_source(source)
    if kind == "new":
        _setup_new(upgrade_skills)
    elif kind == "github":
        project_dir = Path.cwd() / _repo_name_from_source(source)
        console.print(Panel(f"[bold]Attaching GitHub repo:[/bold] {source}", style="blue"))
        if not project_dir.exists():
            r = subprocess.run(["git", "clone", _github_to_clone_url(source), str(project_dir)])
            if r.returncode != 0:
                console.print("[red]git clone failed[/red]")
                sys.exit(1)
        _setup_local(project_dir, upgrade_skills)
    else:
        _setup_local(Path(source).resolve(), upgrade_skills)


@cli.command(hidden=True, deprecated=True)
@click.argument("source", required=False)
@click.option("--upgrade-skills", is_flag=True)
@click.pass_context
def setup(ctx, source, upgrade_skills):
    """Deprecated alias for `sdlc init`."""
    console.print("[yellow]`sdlc setup` is deprecated — use `sdlc init` instead.[/yellow]")
    ctx.invoke(init, source=source, upgrade_skills=upgrade_skills)


def _setup_new(upgrade_skills: bool) -> None:
    console.print(Panel("[bold]New project setup[/bold]", style="blue"))
    name        = click.prompt("Project name")
    description = click.prompt("Description")
    stack       = click.prompt("Tech stack (e.g. Python/FastAPI, Node.js)")
    repo        = click.prompt("GitHub repo owner/name (or Enter to skip)", default="")
    slack       = click.prompt("Slack webhook URL (or Enter to skip)", default="", hide_input=True)

    slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    project_dir = Path.cwd() / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    if not (project_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=project_dir)

    spec = {
        "project_name": name,
        "description": description,
        "tech_stack": stack,
        "repo": repo,
        "slack_webhook": slack,
        "executor": "claude-code",
    }
    _common_setup(project_dir, spec, is_new=True, upgrade_skills=upgrade_skills)


def _setup_local(project_dir: Path, upgrade_skills: bool) -> None:
    if not project_dir.exists():
        console.print(f"[red]Path does not exist: {project_dir}[/red]")
        sys.exit(1)

    is_new = not any(
        f for f in project_dir.iterdir()
        if f.name not in {".git", ".sdlc", ".claude"}
    )
    console.print(Panel(
        f"[bold]{'New' if is_new else 'Existing'} project:[/bold] {project_dir.name}",
        style="blue",
    ))

    # Load or build spec
    existing = project_dir / ".sdlc" / "spec.yaml"
    legacy   = project_dir / "spec.yaml"
    if existing.exists():
        spec = yaml.safe_load(existing.read_text()) or {}
    elif legacy.exists():
        spec = yaml.safe_load(legacy.read_text()) or {}
    else:
        name    = click.prompt("Project name", default=project_dir.name)
        stack   = click.prompt("Tech stack", default=_detect_stack(project_dir) or "")
        repo    = click.prompt("GitHub repo owner/name", default=_detect_remote_repo(project_dir))
        obj_key = "description" if is_new else "objective"
        obj_lbl = "Description" if is_new else "Objective (what work are we doing?)"
        obj     = click.prompt(obj_lbl)
        slack   = click.prompt("Slack webhook URL (or Enter to skip)", default="", hide_input=True)
        spec    = {
            "project_name": name,
            obj_key: obj,
            "tech_stack": stack,
            "repo": repo,
            "slack_webhook": slack,
            "executor": "claude-code",
        }

    start_phase = _detect_starting_phase(project_dir)
    _set_initial_state(project_dir, start_phase)
    _common_setup(project_dir, spec, is_new=is_new, upgrade_skills=upgrade_skills)


# ── sdlc state (Claude-facing) ────────────────────────────────────────────────

@cli.group()
def state():
    """Read and update workflow state (used by Claude during /sdlc-orchestrate)."""
    pass


@state.command("get")
def state_get():
    """Print current state — machine-readable output for Claude."""
    project_dir = _require_project()
    wf  = WorkflowState(project_dir)
    mem = MemoryManager(project_dir)
    spec = mem.spec()

    # Machine-readable block Claude can parse
    artifacts = {k: v for k, v in wf.artifacts.items() if v}
    allowed_next = [s.value for s in TRANSITIONS.get(wf.state, [])]

    click.echo(f"state: {wf.state.value}")
    click.echo(f"label: {STATE_LABELS.get(wf.state, '')}")
    click.echo(f"approval_needed: {wf.approval_needed}")
    click.echo(f"retry_count: {wf.retry_count}")
    click.echo(f"project: {spec.get('project_name', project_dir.name)}")
    click.echo(f"branch: {wf._data.get('current_branch', 'main')}")
    click.echo(f"allowed_transitions: {', '.join(allowed_next)}")
    if wf.blocked_reason:
        click.echo(f"blocked_reason: {wf.blocked_reason}")
    if artifacts:
        click.echo(f"artifacts: {', '.join(artifacts)}")


@state.command("set")
@click.argument("new_state")
@click.option("--force", is_flag=True, help="Skip transition validation")
def state_set(new_state, force):
    """Transition to a new state. Called by Claude after completing a phase."""
    project_dir = _require_project()
    wf = _make_workflow_state(project_dir)
    try:
        target = State(new_state)
    except ValueError:
        valid = [s.value for s in State]
        click.echo(f"Unknown state: {new_state}\nValid states:\n" + "\n".join(f"  {s}" for s in valid), err=True)
        sys.exit(1)

    if force:
        wf._data["state"] = target.value
        wf._data["approval_needed"] = target in APPROVAL_STATES
        wf.save()
    else:
        try:
            wf.transition(target)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)

    click.echo(f"ok: {wf.state.value}")


@state.command("approve")
def state_approve():
    """Advance past the current approval gate. Fallback for projects without GitHub."""
    project_dir = _require_project()
    wf = _make_workflow_state(project_dir)

    if wf.state not in APPROVAL_STATES:
        click.echo(f"Not an approval gate: {wf.state.value}", err=True)
        sys.exit(1)

    NEXT: dict[State, State] = {
        State.AWAITING_REQUIREMENT_ANSWER:    State.REQUIREMENT_IN_PROGRESS,
        State.REQUIREMENT_READY_FOR_APPROVAL: State.DESIGN_IN_PROGRESS,
        State.AWAITING_DESIGN_APPROVAL:       State.TASK_PLAN_IN_PROGRESS,
        State.TASK_PLAN_READY:                State.IMPLEMENTATION_IN_PROGRESS,
        State.AWAITING_REVIEW:                State.DONE,
        State.BLOCKED:                        State.DRAFT_REQUIREMENT,
    }
    next_state = NEXT[wf.state]
    wf.transition(next_state)
    click.echo(f"approved: {next_state.value}")
    console.print(f"[green]✓ Approved.[/green] → {STATE_LABELS.get(next_state, next_state.value)}")
    console.print("  Tell Claude to continue (/sdlc-orchestrate).")


@state.command("history")
def state_history():
    """Print state transition history."""
    project_dir = _require_project()
    wf = WorkflowState(project_dir)
    for h in wf._data.get("history", []):
        click.echo(f"{h['timestamp'][:19]}  {h['state']}")


# ── sdlc artifact (Claude-facing) ─────────────────────────────────────────────

@cli.group()
def artifact():
    """Read and list phase artifacts (used by Claude during /sdlc-orchestrate)."""
    pass


@artifact.command("read")
@click.argument("name")
def artifact_read(name):
    """Print an artifact to stdout. Claude uses this to read phase outputs."""
    project_dir = _require_project()
    mem = MemoryManager(project_dir)
    content = mem.artifact(name)
    if not content:
        click.echo(f"Artifact not found: {name}", err=True)
        sys.exit(1)
    click.echo(content)


@artifact.command("list")
def artifact_list():
    """List available artifacts."""
    project_dir = _require_project()
    home = sdlc_home(project_dir)
    artifacts_dir = home / "workflow" / "artifacts"
    if not artifacts_dir.exists():
        click.echo("No artifacts yet.")
        return
    for f in sorted(artifacts_dir.glob("*.md")):
        size = f.stat().st_size
        click.echo(f"  {f.stem:30}  {size:>6} bytes")


# ── sdlc notify (Claude-facing) ───────────────────────────────────────────────

@cli.command()
@click.argument("phase")
@click.argument("event")
def notify(phase, event):
    """Send a Slack notification. Called by Claude at approval gates."""
    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    from sdlc_orchestrator.integrations.slack import notify_from_spec
    notify_from_spec(spec, phase, event)
    click.echo(f"notified: {phase}/{event}")


# ── sdlc github (Claude-facing) ───────────────────────────────────────────────

@cli.group()
def github():
    """GitHub operations. Called by Claude during /sdlc-orchestrate."""
    pass


@github.command("create-pr")
@click.argument("branch")
@click.argument("phase")
def github_create_pr(branch, phase):
    """Create a GitHub PR for the current phase branch."""
    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    if not repo:
        click.echo("No repo in spec.yaml", err=True)
        sys.exit(1)

    # Push branch first
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=project_dir, capture_output=True)

    from sdlc_orchestrator.integrations.github import create_pr
    url = create_pr(
        repo=repo, phase=phase, branch=branch,
        body=(
            f"## SDLC Phase: `{phase}`\n\n"
            f"Automated output. Review then run `sdlc state approve`.\n\n"
            f"> Generated by /sdlc-orchestrate"
        ),
    )
    click.echo(url or "PR creation skipped (may already exist)")


@github.command("create-issue")
@click.argument("title")
@click.argument("body_file", required=False)
def github_create_issue(title, body_file):
    """Create a GitHub issue. Body read from file or stdin."""
    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    if not repo:
        click.echo("No repo in spec.yaml", err=True)
        sys.exit(1)

    if body_file and Path(body_file).exists():
        body = Path(body_file).read_text()
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        body = title

    from sdlc_orchestrator.integrations.github import create_child_issue
    issue_num = create_child_issue(repo, title, body)
    click.echo(f"issue: {issue_num or 'failed'}")


@github.command("pr-status")
@click.argument("branch")
def github_pr_status(branch):
    """Check if the PR for a branch is approved or merged. Used by Claude to poll gates."""
    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    if not repo:
        click.echo("no-repo")
        sys.exit(0)

    from sdlc_orchestrator.integrations.github import get_pr_status
    status = get_pr_status(repo, branch)
    click.echo(status or "not-found")


@github.command("ingest-feedback")
@click.argument("branch")
@click.argument("phase")
def github_ingest_feedback(branch, phase):
    """Pull PR review comments into .sdlc/feedback/<phase>.md. Called by Claude before advancing."""
    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    if not repo:
        click.echo("skipped: no repo configured")
        return

    from sdlc_orchestrator.integrations.github import pull_pr_feedback
    feedback_dir = sdlc_home(project_dir) / "feedback"
    count = pull_pr_feedback(repo, branch, feedback_dir, phase)
    click.echo(f"ingested: {count} comment(s) → .sdlc/feedback/{phase}.md")


@github.command("create-board")
def github_create_board():
    """Create the GitHub project board for this project."""
    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    _setup_github_board(project_dir, spec)


# ── sdlc status (human-facing) ────────────────────────────────────────────────

@cli.command()
def status():
    """Show current SDLC state (human-readable)."""
    project_dir = _require_project()
    wf   = WorkflowState(project_dir)
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


# ── sdlc answer (human-facing) ────────────────────────────────────────────────

@cli.command()
@click.option("--file", "from_file", is_flag=True,
              help="Answers already written in the questions file")
def answer(from_file):
    """Submit answers to requirement clarifying questions."""
    project_dir = _require_project()
    wf = WorkflowState(project_dir)

    if wf.state != State.AWAITING_REQUIREMENT_ANSWER:
        console.print(f"[yellow]Not awaiting answers. Current: {wf.state.value}[/yellow]")
        return

    home = sdlc_home(project_dir)
    questions_file = home / "workflow" / "artifacts" / "requirement_questions.md"
    if not questions_file.exists():
        console.print("[red]requirement_questions.md not found. Ask Claude to run /sdlc-orchestrate first.[/red]")
        return

    if not from_file:
        content = questions_file.read_text()
        console.print(Panel("[bold]Requirement Clarifying Questions[/bold]", style="blue"))
        answers: list[str] = []
        i, lines = 0, content.splitlines()
        while i < len(lines):
            line = lines[i]
            if line.startswith("## Q"):
                console.print(f"\n[bold]{line}[/bold]")
                i += 1
                while i < len(lines) and not lines[i].startswith("**Answer:**") \
                        and not lines[i].startswith("## Q"):
                    if lines[i].strip():
                        console.print(f"  {lines[i]}")
                    i += 1
                ans = click.prompt("  Your answer")
                answers.append(f"\n{line}\n**Answer:** {ans}\n")
            else:
                i += 1
        questions_file.write_text("# Requirement Questions & Answers\n\n" + "\n".join(answers))
        console.print("[green]✓ Answers saved.[/green]")

    wf.transition(State.REQUIREMENT_IN_PROGRESS)
    console.print("  Tell Claude to continue (/sdlc-orchestrate).")


# ── sdlc tick (loop guard) ────────────────────────────────────────────────────

@cli.group()
def tick():
    """Tick-lock helpers — prevent concurrent /sdlc-orchestrate runs."""
    pass


@tick.command("acquire")
def tick_acquire():
    """Acquire the tick lock. Exits non-zero if already locked."""
    import fcntl
    project_dir = _require_project()
    lock_path = sdlc_home(project_dir) / "workflow" / "tick.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Write PID so release can verify ownership
        lock_fh.write(str(subprocess.os.getpid()) if hasattr(subprocess, "os") else "")
        lock_fh.flush()
        click.echo("ok: lock acquired")
        # Keep the file handle open — lock held until process exits
        # Store path for release command
        pid_file = lock_path.with_suffix(".pid")
        import os
        pid_file.write_text(str(os.getpid()))
    except BlockingIOError:
        click.echo("locked: another tick is running", err=True)
        sys.exit(1)


@tick.command("release")
def tick_release():
    """Release the tick lock."""
    project_dir = _require_project()
    lock_path = sdlc_home(project_dir) / "workflow" / "tick.lock"
    pid_file = lock_path.with_suffix(".pid")
    for p in (lock_path, pid_file):
        p.unlink(missing_ok=True)
    click.echo("ok: lock released")


# ── sdlc relink ───────────────────────────────────────────────────────────────

@cli.command()
@click.option("--all", "relink_all", is_flag=True)
def relink(relink_all):
    """Rebuild ~/.sdlc/projects/<slug> symlink."""
    if relink_all:
        projects_dir = Path.home() / ".sdlc" / "projects"
        if not projects_dir.exists():
            console.print("[yellow]No ~/.sdlc/projects/ found.[/yellow]")
            return
        for link in projects_dir.iterdir():
            status_str = "[green]ok[/green]" if (link.is_symlink() and link.exists()) \
                else "[red]broken[/red]"
            target = link.readlink() if link.is_symlink() else "?"
            console.print(f"  {status_str}  {link.name} → {target}")
        return

    project_dir = _require_project()
    link = create_symlink(project_dir)
    console.print(f"[green]✓[/green] ~/.sdlc/projects/{project_slug(project_dir)} → {link.resolve()}")

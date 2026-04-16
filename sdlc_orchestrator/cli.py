"""
sdlc CLI — human tools and Claude's state/integration toolbox.

Human-facing:
  sdlc init [source]        scaffold a project (new or existing)
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
  sdlc story start <STORY-NNN>          set active story and begin implementation
  sdlc story complete                   mark story approved, advance to next or done
  sdlc github create-story-issues       create one issue per STORY-NNN in plan.md

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
        console.print("[red]No SDLC project found. Run [bold]sdlc init .[/bold] first.[/red]")
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

def _trigger_agent(project_dir: Path, skill: str = "sdlc-orchestrate") -> subprocess.CompletedProcess | None:
    """Spawn a headless agent tick for the project's configured executor.
    Returns None if the executor has no headless CLI (e.g. cline)."""
    from sdlc_orchestrator.memory import EXECUTOR_CLI, MemoryManager
    spec = MemoryManager(project_dir).spec()
    executor = spec.get("executor", "claude-code")
    cmd_template = EXECUTOR_CLI.get(executor, EXECUTOR_CLI["claude-code"])
    if not cmd_template:
        return None
    cmd = [part.replace("{skill}", skill) for part in cmd_template]
    return subprocess.run(cmd, cwd=str(project_dir))
    from sdlc_orchestrator.memory import executor_config
    _, dest_dir, _ = executor_config(executor)
    dest_dir.mkdir(parents=True, exist_ok=True)
    from importlib.resources import files as _files
    try:
        skills_pkg = _files("sdlc_orchestrator") / "skills"
        for skill_file in skills_pkg.iterdir():  # type: ignore[attr-defined]
            dest = dest_dir / skill_file.name
            if not dest.exists() or force:
                dest.write_text(skill_file.read_text(encoding="utf-8"))
                console.print(f"  [dim]skill:[/dim] {dest_dir}/{skill_file.name}")
    except Exception as e:
        console.print(f"  [yellow]Skill install warning: {e}[/yellow]")

def _write_hooks(project_dir: Path, executor: str = "claude-code") -> None:
    import json
    from sdlc_orchestrator.memory import executor_config
    _, _, settings_rel = executor_config(executor)
    # settings_rel is a relative Path like Path(".claude") — make it absolute
    agent_dir = project_dir / settings_rel
    agent_dir.mkdir(exist_ok=True)
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
    (agent_dir / "settings.json").write_text(json.dumps(settings, indent=2))

def _init_sdlc_dirs(project_dir: Path) -> None:
    home = sdlc_home(project_dir)
    for sub in ["memory", "workflow/artifacts", "workflow/logs", "feedback"]:
        (home / sub).mkdir(parents=True, exist_ok=True)

def _set_initial_state(project_dir: Path, phase: str) -> None:
    phase_map = {
        "requirement":    State.REQUIREMENT_IN_PROGRESS,
        "design":         State.DESIGN_IN_PROGRESS,
        "planning":       State.TASK_PLAN_IN_PROGRESS,
        "implementation": State.STORY_IN_PROGRESS,
        "validation":     State.STORY_IN_PROGRESS,
        "review":         State.STORY_AWAITING_REVIEW,
    }
    wf = WorkflowState(project_dir)
    wf._data["state"] = phase_map.get(phase, State.REQUIREMENT_IN_PROGRESS).value
    wf.save()

def _detect_starting_phase(project_dir: Path) -> str:
    return "requirement"

def _setup_github_board(project_dir: Path, spec: dict) -> None:
    from sdlc_orchestrator.integrations import github
    repo = spec.get("repo", "")
    if not repo or not github.is_available():
        return
    wf = WorkflowState(project_dir)
    if wf.github_project or wf.github_project_id:
        console.print("  [dim]GitHub project board already exists — skipped[/dim]")
        return
    project_info = github.create_project_board(spec.get("project_name", ""), repo)
    if project_info:
        wf.set_github_project(project_info)
        wf.save()
        console.print(f"  [green]✓[/green] GitHub project board #{project_info['number']}")
    else:
        console.print("  [yellow]GitHub board skipped (check gh auth scope: project)[/yellow]")

def _run_analyze_skill(project_dir: Path, spec: dict) -> None:
    from sdlc_orchestrator.memory import executor_config
    executor = spec.get("executor", "claude-code")
    _, skills_dir, _ = executor_config(executor)
    skill_path = skills_dir / "sdlc-analyze-repo.md"
    if not skill_path.exists():
        _install_global_skills(executor=executor)
    prompt = skill_path.read_text() if skill_path.exists() else (
        "Analyze this repo. Write .sdlc/memory/project.md covering stack, "
        "architecture, domain concepts, testing, tech debt, deployment."
    )
    prompt = prompt.replace("{{PROJECT_NAME}}", spec.get("project_name", ""))
    try:
        result = _trigger_agent(project_dir, skill="sdlc-analyze-repo")
        if result is None:
            console.print("  [yellow]Repo analysis skipped (executor has no headless CLI)[/yellow]")
        elif result.returncode == 0:
            console.print("  [green]✓[/green] Repo analysis complete")
        else:
            console.print("  [yellow]Repo analysis had warnings — edit .sdlc/memory/project.md[/yellow]")
    except Exception:
        console.print("  [yellow]Repo analysis skipped (agent CLI not found)[/yellow]")

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

    executor = spec.get("executor", "claude-code")

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
    context_filename = mem.context_file_path.name
    console.print(f"  [green]✓[/green] {context_filename}")

    WorkflowState(project_dir).save()
    console.print("  [green]✓[/green] .sdlc/workflow/state.json")

    from sdlc_orchestrator.memory import executor_config
    _, skills_dir, settings_rel = executor_config(executor)
    _write_hooks(project_dir, executor)
    console.print(f"  [green]✓[/green] {settings_rel}/settings.json")

    _install_global_skills(force=upgrade_skills, executor=executor)
    console.print(f"  [green]✓[/green] skills → {skills_dir}/  (including /sdlc-orchestrate)")

    update_gitignore(project_dir)
    console.print("  [green]✓[/green] .gitignore")

    _setup_github_board(project_dir, spec)

    console.print(f"""
[bold green]Ready.[/bold green]

  cd {project_dir}

  Next steps:
    1. Open your agent ({executor}) in this directory
    2. Run [bold]/sdlc-setup[/bold] — agent interviews you and drafts requirements
    3. Review requirements, then run [bold]/sdlc-start[/bold]

  The agent drives the rest. You'll get Slack pings at each approval gate.
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



def _setup_new(upgrade_skills: bool) -> None:
    console.print(Panel("[bold]New project setup[/bold]", style="blue"))
    name = click.prompt("Project name (used for directory name)")
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    project_dir = Path.cwd() / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    if not (project_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=project_dir)

    spec = {
        "project_name": name,
        "description": "",
        "tech_stack": "",
        "repo": "",
        "slack_webhook": "",
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

    # Load or build spec — never prompt, Claude handles this in /sdlc-setup
    existing = project_dir / ".sdlc" / "spec.yaml"
    if existing.exists():
        spec = yaml.safe_load(existing.read_text()) or {}
    else:
        spec = {
            "project_name": project_dir.name,
            "description": "",
            "tech_stack": _detect_stack(project_dir),
            "repo": _detect_remote_repo(project_dir),
            "slack_webhook": "",
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
    if wf.current_story:
        click.echo(f"current_story: {wf.current_story}")
    if wf.completed_stories:
        click.echo(f"completed_stories: {', '.join(wf.completed_stories)}")
    story_ids = sorted(wf.github_story_items.keys())
    pending = [s for s in story_ids if s not in wf.completed_stories]
    if pending:
        click.echo(f"pending_stories: {', '.join(pending)}")
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
        State.REQUIREMENT_READY_FOR_APPROVAL: State.DESIGN_IN_PROGRESS,
        State.AWAITING_DESIGN_APPROVAL:       State.TASK_PLAN_IN_PROGRESS,
        State.TASK_PLAN_READY:                State.STORY_IN_PROGRESS,
        State.STORY_AWAITING_REVIEW:          State.STORY_IN_PROGRESS,
        State.BLOCKED:                        State.REQUIREMENT_IN_PROGRESS,
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


# ── sdlc story (Claude-facing) ───────────────────────────────────────────────

@cli.group()
def story():
    """Manage per-story progress during the implementation phase."""
    pass


@story.command("start")
@click.argument("story_id")
def story_start(story_id):
    """Set the active story and transition to story_in_progress.

    Claude calls this when beginning work on a new story.
    """
    project_dir = _require_project()
    wf = _make_workflow_state(project_dir)
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
    """Mark the current story as approved and advance to the next story or done.

    Claude calls this after a story PR is approved. Prints 'next: STORY-NNN'
    if more stories remain, or 'all_complete' when the last story is done.
    """
    project_dir = _require_project()
    wf = _make_workflow_state(project_dir)
    current = wf.current_story
    if not current:
        click.echo("error: no current_story set", err=True)
        sys.exit(1)

    wf.complete_current_story()

    # Determine pending stories from board items
    all_stories = sorted(wf.github_story_items.keys())
    pending = [s for s in all_stories if s not in wf.completed_stories]

    if pending:
        next_story = pending[0]
        click.echo(f"next: {next_story}")
    else:
        click.echo("all_complete")


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

    subprocess.run(["git", "push", "-u", "origin", branch], cwd=project_dir, capture_output=True)

    wf = WorkflowState(project_dir)

    # Collect issues this PR closes: story issue + its task issues
    closes: list[int] = []
    story_item = wf.github_story_items.get(phase, {})  # phase may be STORY-NNN
    if story_item.get("number"):
        closes.append(story_item["number"])
    # Also close task issues belonging to this story (from plan.md task_ids)
    from sdlc_orchestrator.integrations import github as gh_mod
    plan_path = project_dir / "docs/sdlc/plan.md"
    if plan_path.exists():
        stories = gh_mod.parse_plan_stories(plan_path.read_text())
        for s in stories:
            if s["id"] == phase:
                for task_id in s["task_ids"]:
                    t = wf.github_task_items.get(task_id, {})
                    if t.get("number"):
                        closes.append(t["number"])
                break

    from sdlc_orchestrator.integrations.github import create_pr
    url = create_pr(
        repo=repo, phase=phase, branch=branch,
        body=(
            f"## SDLC Phase: `{phase}`\n\n"
            f"Automated output. Review then approve.\n\n"
            f"> Generated by /sdlc-orchestrate"
        ),
        closes_issues=closes,
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


@github.command("setup")
def github_setup():
    """Full GitHub setup: labels, project board, workflows, and phase issues.

    Run once after sdlc init (or re-run to repair). Creates:
      - SDLC labels (sdlc:requirement, sdlc:design, etc.)
      - GitHub Projects v2 board with Status field
      - Built-in workflow automations (item closed → Done, PR merged → Done, etc.)
      - One issue per SDLC phase, added to the board in Backlog
    """
    from sdlc_orchestrator.integrations import github as gh

    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")

    if not repo:
        console.print("[red]No repo in spec.yaml — set repo: owner/repo first.[/red]")
        sys.exit(1)

    if not gh.is_available():
        console.print("[red]gh CLI not found or not authenticated.[/red]")
        sys.exit(1)

    project_name = spec.get("project_name", project_dir.name)
    wf = WorkflowState(project_dir)

    # 1. Labels
    console.print("  Creating labels...")
    created = gh.setup_labels(repo)
    console.print(f"  [green]✓[/green] {len(created)} label(s) ready")

    # 2. Project board
    project_info = wf.github_project
    if project_info:
        console.print(f"  [dim]Board #{project_info.get('number')} already exists — skipped[/dim]")
    else:
        console.print("  Creating project board...")
        project_info = gh.create_project_board(project_name, repo)
        if not project_info:
            console.print("  [yellow]Project board creation failed — check gh auth scope (project).[/yellow]")
        else:
            wf.set_github_project(project_info)
            wf.save()
            console.print(
                f"  [green]✓[/green] Board #{project_info['number']} · "
                f"{len(project_info.get('status_options', {}))} status columns"
            )

    # 3. Workflow automations
    if project_info and project_info.get("node_id"):
        console.print("  Enabling workflow automations...")
        enabled = gh.enable_project_workflows(project_info["node_id"])
        if enabled:
            console.print(f"  [green]✓[/green] Workflows: {', '.join(enabled)}")
        else:
            console.print("  [dim]Workflows may already be enabled or require manual setup in GitHub UI.[/dim]")

    # 4. Epic issue
    if wf.github_epic_issue:
        console.print(f"  [dim]Epic #{wf.github_epic_issue} already exists — skipped[/dim]")
    else:
        console.print("  Creating epic issue...")
        spec_desc = spec.get("description", "")
        epic_body = f"**{project_name}**\n\n{spec_desc}\n\nThis epic tracks all SDLC phases for this project."
        epic_number = gh.create_epic(repo, project_name, epic_body)
        if epic_number:
            wf.set_github(epic_issue=epic_number)
            console.print(f"  [green]✓[/green] Epic #{epic_number}")
        else:
            console.print("  [yellow]Epic creation failed — continuing[/yellow]")

    # 5. Phase issues
    console.print("  Creating phase issues...")
    phases = [
        ("requirement",    f"[sdlc:requirement] Requirements"),
        ("design",         f"[sdlc:design] System Design"),
        ("planning",       f"[sdlc:plan] Task Plan"),
        ("implementation", f"[sdlc:implementation] Implementation"),
        ("testing",        f"[sdlc:testing] Testing & Validation"),
        ("review",         f"[sdlc:review] Code Review"),
    ]
    for phase, title in phases:
        if wf.github_phase_items.get(phase):
            continue  # already created
        info = gh.create_phase_issue(
            repo=repo,
            phase=phase,
            title=title,
            body=f"Tracks the **{phase}** phase of {project_name}.\n\nArtifact will be linked when this phase begins.",
            project_info=project_info,
        )
        if info.get("number"):
            wf.set_phase_item(phase, info["number"], info.get("item_id", ""))
            console.print(f"  [green]✓[/green] #{info['number']} {title}")

    console.print("\n[bold green]GitHub setup complete.[/bold green]")
    if project_info:
        console.print(
            f"  Board: https://github.com/orgs/{repo.split('/')[0]}/projects/{project_info['number']}"
        )


@github.command("sync-board")
def github_sync_board():
    """Move the active phase issue to the correct board column based on current state.

    Claude calls this after every sdlc state set to keep the board in sync.
    """
    from sdlc_orchestrator.integrations import github as gh

    # State → (board status, phase whose issue to move)
    # Story states are handled separately below using current_story.
    STATE_BOARD: dict[str, tuple[str, str]] = {
        "requirement_in_progress":        ("In Progress",     "requirement"),
        "requirement_ready_for_approval": ("Awaiting Review", "requirement"),
        "design_in_progress":             ("In Progress",     "design"),
        "awaiting_design_approval":       ("Awaiting Review", "design"),
        "task_plan_in_progress":          ("In Progress",     "planning"),
        "task_plan_ready":                ("Awaiting Review", "planning"),
        "feedback_incorporation":         ("In Progress",     ""),   # story handled below
        "blocked":                        ("Blocked",         ""),
        "done":                           ("Done",            ""),
    }

    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    wf = WorkflowState(project_dir)
    project_info = wf.github_project

    if not repo or not project_info:
        click.echo("skipped: no repo or project board configured")
        return

    # Auto-close issues for any merged story PRs
    closed = gh.close_merged_pr_issues(
        repo=repo,
        story_items=wf.github_story_items,
        task_items=wf.github_task_items,
        project_info=project_info,
    )
    if closed:
        click.echo(f"auto-closed merged stories: {', '.join(closed)}")

    board_status, phase = STATE_BOARD.get(wf.state.value, ("In Progress", ""))

    def _ensure_phase_on_board(item: dict, p: str) -> str:
        """Return item_id for a phase issue, adding to board first if missing."""
        item_id = item.get("item_id", "")
        issue_number = item.get("issue")
        if not item_id and issue_number and project_info:
            node_id = gh._get_issue_node_id(repo, issue_number)
            if node_id:
                item_id = gh.add_to_project(project_info["node_id"], node_id)
                if item_id:
                    wf.set_phase_item(p, issue_number, item_id)
                    click.echo(f"added #{issue_number} to board")
        return item_id

    def _ensure_story_on_board(story_id: str, item: dict) -> str:
        """Return item_id for a story issue, adding to board first if missing."""
        item_id = item.get("item_id", "")
        issue_number = item.get("number") or item.get("issue")
        if not item_id and issue_number and project_info:
            node_id = gh._get_issue_node_id(repo, issue_number)
            if node_id:
                item_id = gh.add_to_project(project_info["node_id"], node_id)
                if item_id:
                    wf.set_story_items({story_id: {"number": issue_number, "item_id": item_id}})
                    click.echo(f"added #{issue_number} ({story_id}) to board")
        return item_id

    if wf.state.value == "done":
        # Move all phase issues to Done and close them
        for p, item in wf.github_phase_items.items():
            item_id = _ensure_phase_on_board(item, p)
            if item_id:
                gh.move_phase_issue(project_info, item_id, "Done")
            issue_number = item.get("issue")
            if issue_number:
                gh.close_issue(repo, issue_number, comment="Closed automatically — SDLC workflow complete.")
        # Move all story issues to Done and close them
        for story_id, item in wf.github_story_items.items():
            item_id = _ensure_story_on_board(story_id, item)
            if item_id:
                gh.move_phase_issue(project_info, item_id, "Done")
            issue_number = item.get("number") or item.get("issue")
            if issue_number:
                gh.close_issue(repo, issue_number, comment=f"Closed automatically — {story_id} complete.")
        # Close all task issues too
        for task_id, item in wf.github_task_items.items():
            issue_number = item.get("number") or item.get("issue")
            if issue_number:
                gh.close_issue(repo, issue_number, comment=f"Closed automatically — {task_id} complete.")
        click.echo("synced: all phases and stories → Done, issues closed")
        return

    # Story states: move current story's board item
    if wf.state.value in ("story_in_progress", "story_awaiting_review"):
        story_board_status = "In Progress" if wf.state.value == "story_in_progress" else "Awaiting Review"
        current = wf.current_story
        if not current:
            click.echo("skipped: no current_story set (run 'sdlc story start STORY-NNN' first)")
            return
        story_item = wf.github_story_items.get(current, {})
        item_id = _ensure_story_on_board(current, story_item)
        if not item_id:
            click.echo(f"skipped: no board item for {current}")
            return
        ok = gh.move_phase_issue(project_info, item_id, story_board_status)
        click.echo(f"{'synced' if ok else 'failed'}: {current} → {story_board_status}")
        return

    if not phase:
        click.echo(f"skipped: no phase mapped for state {wf.state.value}")
        return

    item = wf.github_phase_items.get(phase, {})
    item_id = _ensure_phase_on_board(item, phase)
    if not item_id:
        click.echo(f"skipped: no board item for phase {phase} (run 'sdlc github setup' first)")
        return

    ok = gh.move_phase_issue(project_info, item_id, board_status)
    click.echo(f"{'synced' if ok else 'failed'}: {phase} → {board_status}")


@github.command("close-merged")
def github_close_merged():
    """Close story + task issues whose PR branch is merged. Called by sync-board automatically.

    For each story in github_story_items whose sdlc/story-NNN PR is merged:
      - closes the story GitHub issue
      - closes all sub-task GitHub issues
      - moves story + task board items to Done
    """
    from sdlc_orchestrator.integrations import github as gh

    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    wf = WorkflowState(project_dir)

    if not repo:
        click.echo("skipped: no repo configured")
        return

    closed = gh.close_merged_pr_issues(
        repo=repo,
        story_items=wf.github_story_items,
        task_items=wf.github_task_items,
        project_info=wf.github_project,
    )
    if closed:
        click.echo(f"closed: {', '.join(closed)}")
    else:
        click.echo("no merged story PRs found")


@click.argument("plan_file", default="docs/sdlc/plan.md")
def github_create_task_issues(plan_file):
    """Parse plan.md and create one GitHub issue per task, added to the board.

    Claude calls this after the task plan is approved and before implementation
    begins. Each TASK-NNN becomes a tracked issue on the project board.
    """
    from sdlc_orchestrator.integrations import github as gh

    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    wf = WorkflowState(project_dir)
    project_info = wf.github_project

    if not repo:
        click.echo("skipped: no repo configured")
        return

    plan_path = project_dir / plan_file
    if not plan_path.exists():
        click.echo(f"error: plan file not found at {plan_file}", err=True)
        sys.exit(1)

    tasks = gh.parse_plan_tasks(plan_path.read_text())
    if not tasks:
        click.echo("no tasks found in plan.md")
        return

    # Skip tasks already created
    existing = wf.github_task_items
    new_tasks = [t for t in tasks if t["id"] not in existing]

    if not new_tasks:
        click.echo(f"all {len(tasks)} task issues already exist")
        return

    console.print(f"  Creating {len(new_tasks)} task issue(s)...")
    created = gh.create_task_issues(
        repo=repo,
        tasks=new_tasks,
        project_info=project_info,
        epic_issue=wf.github_epic_issue,
    )
    wf.set_task_items(created)

    for task_id, info in created.items():
        console.print(f"  [green]✓[/green] #{info['number']} {task_id}")
    click.echo(f"created: {len(created)} issue(s)")


@github.command("create-story-issues")
@click.argument("plan_file", default="docs/sdlc/plan.md")
def github_create_story_issues(plan_file):
    """Parse plan.md and create one GitHub issue per STORY-NNN, added to the board.

    Claude calls this after the task plan is approved. Each STORY-NNN becomes a
    tracked issue that groups its TASK-NNN children on the project board.
    """
    from sdlc_orchestrator.integrations import github as gh

    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    wf = WorkflowState(project_dir)
    project_info = wf.github_project

    if not repo:
        click.echo("skipped: no repo configured")
        return

    plan_path = project_dir / plan_file
    if not plan_path.exists():
        click.echo(f"error: plan file not found at {plan_file}", err=True)
        sys.exit(1)

    stories = gh.parse_plan_stories(plan_path.read_text())
    if not stories:
        click.echo("no stories found in plan.md (add # STORY-NNN: Title sections)")
        return

    existing = wf.github_story_items
    new_stories = [s for s in stories if s["id"] not in existing]

    if not new_stories:
        click.echo(f"all {len(stories)} story issues already exist")
        return

    console.print(f"  Creating {len(new_stories)} story issue(s)...")
    # Offset so plan STORY-001 becomes STORY-004 if 3 phase stories already exist
    phase_story_count = len([p for p in wf.github_phase_items if wf.github_phase_items[p].get("issue")])
    created = gh.create_story_issues(
        repo=repo,
        stories=new_stories,
        project_info=project_info,
        epic_issue=wf.github_epic_issue,
        story_number_offset=phase_story_count,
        task_issue_map=wf.github_task_items,
    )
    wf.set_story_items(created)

    for story_id, info in created.items():
        console.print(f"  [green]✓[/green] #{info['number']} {story_id}")
    click.echo(f"created: {len(created)} story issue(s)")


@github.command("close-phase-issue")
@click.argument("phase")
@click.option("--comment", default="", help="Optional closing comment")
def github_close_phase_issue(phase, comment):
    """Close the GitHub issue for a completed phase."""
    from sdlc_orchestrator.integrations import github as gh
    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")
    if not repo:
        click.echo("skipped: no repo configured")
        return
    wf = WorkflowState(project_dir)
    item = wf.github_phase_items.get(phase, {})
    issue_number = item.get("issue")
    if not issue_number:
        click.echo(f"skipped: no issue found for phase {phase}")
        return
    gh.close_issue(repo, issue_number, comment=comment or f"Phase {phase} complete.")
    click.echo(f"closed: #{issue_number} ({phase})")



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




# ── sdlc watch ───────────────────────────────────────────────────────────────

# Maps each approval gate to the branch Claude should poll
_GATE_BRANCHES: dict[str, str] = {
    "requirement_ready_for_approval": "sdlc/requirements",
    "awaiting_design_approval":       "sdlc/design",
    "task_plan_ready":                "sdlc/plan",
    "story_awaiting_review":          "",   # branch is dynamic: sdlc/<current_story>
    "blocked":                        "",
}


@cli.command()
@click.option("--interval", default=30, show_default=True,
              help="Seconds between GitHub polls")
def watch(interval):
    """Watch for PR approval and resume Claude automatically.

    Runs in the foreground. Start it in a separate terminal or background it
    with '&'. When it detects a PR approval or merge, it triggers Claude to
    continue the SDLC workflow without any manual sdlc state approve.

    \b
    Typical use:
      Terminal 1: sdlc watch
      Terminal 2: /loop 10m /sdlc-orchestrate   (inside Claude Code)
    """
    import time
    import os

    project_dir = _require_project()
    spec = MemoryManager(project_dir).spec()
    repo = spec.get("repo", "")

    if not repo:
        console.print("[yellow]No GitHub repo in spec.yaml — watch has nothing to poll.[/yellow]")
        console.print("  Set [bold]repo: owner/repo[/bold] in .sdlc/spec.yaml and re-run.")
        sys.exit(1)

    from sdlc_orchestrator.integrations.github import get_pr_status, is_available
    if not is_available():
        console.print("[red]gh CLI not found or not authenticated. Run: gh auth login[/red]")
        sys.exit(1)

    console.print(f"[bold]sdlc watch[/bold] — polling GitHub every {interval}s")
    console.print(f"  repo: [dim]{repo}[/dim]")
    console.print("  Press Ctrl-C to stop.\n")

    last_state: str = ""
    last_pr_status: str = ""

    while True:
        try:
            wf = WorkflowState(project_dir)
            current_state = wf.state.value

            if wf.state == State.DONE:
                console.print("[green]✓ Workflow complete.[/green]")
                break

            branch = _GATE_BRANCHES.get(current_state, "")

            if not branch:
                # Not at an approval gate — nothing to watch right now
                if current_state != last_state:
                    console.print(f"[dim]{current_state}[/dim] — Claude is working, not at a gate")
                    last_state = current_state
                time.sleep(interval)
                continue

            pr_status = get_pr_status(repo, branch) or "not-found"

            if current_state != last_state or pr_status != last_pr_status:
                console.print(f"[dim]{current_state}[/dim]  PR [{branch}]: [bold]{pr_status}[/bold]")
                last_state = current_state
                last_pr_status = pr_status

            if pr_status in ("approved", "merged"):
                console.print(f"\n[green]✓ PR approved[/green] — triggering agent to continue...")
                result = _trigger_agent(project_dir)
                if result is None:
                    console.print("[yellow]Executor has no headless CLI — open your agent and run /sdlc-orchestrate manually.[/yellow]")
                elif result.returncode != 0:
                    console.print("[yellow]Agent exited with an error.[/yellow]")
                last_pr_status = ""  # Reset so we re-evaluate next state

            time.sleep(interval)

        except KeyboardInterrupt:
            console.print("\n[dim]watch stopped.[/dim]")
            break


# ── sdlc webhook ──────────────────────────────────────────────────────────────

@cli.command()
@click.option("--port", default=8080, show_default=True)
@click.option("--secret", default="", envvar="SDLC_WEBHOOK_SECRET",
              help="GitHub webhook secret (or set SDLC_WEBHOOK_SECRET env var)")
def webhook(port, secret):
    """Start a GitHub webhook receiver for real-time PR event triggers.

    Configure a GitHub webhook on your repo pointing to:
      http://<your-host>:<port>/webhook

    Events handled: pull_request (closed+merged), pull_request_review (approved)

    \b
    For local development, expose with ngrok:
      ngrok http 8080
      Then set the ngrok URL as the GitHub webhook URL.
    """
    import hashlib
    import hmac
    import json as _json
    from http.server import BaseHTTPRequestHandler, HTTPServer

    project_dir = _require_project()

    class WebhookHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # suppress default access log
            pass

        def do_POST(self):
            if self.path != "/webhook":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            # Verify signature if secret is set
            if secret:
                sig_header = self.headers.get("X-Hub-Signature-256", "")
                expected = "sha256=" + hmac.new(
                    secret.encode(), body, hashlib.sha256
                ).hexdigest()
                if not hmac.compare_digest(sig_header, expected):
                    self.send_response(401)
                    self.end_headers()
                    return

            try:
                payload = _json.loads(body)
            except _json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return

            event = self.headers.get("X-GitHub-Event", "")
            triggered = False

            # PR merged
            if event == "pull_request":
                action = payload.get("action")
                merged = payload.get("pull_request", {}).get("merged", False)
                branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
                if action == "closed" and merged and branch.startswith("sdlc/"):
                    console.print(f"[green]✓ PR merged:[/green] {branch} — triggering Claude")
                    triggered = True

            # PR review approved
            elif event == "pull_request_review":
                state = payload.get("review", {}).get("state", "").upper()
                branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
                if state == "APPROVED" and branch.startswith("sdlc/"):
                    console.print(f"[green]✓ PR approved:[/green] {branch} — triggering Claude")
                    triggered = True

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

            if triggered:
                result = _trigger_agent(project_dir)
                if result is None:
                    console.print("[yellow]Executor has no headless CLI — open your agent and run /sdlc-orchestrate manually.[/yellow]")

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    console.print(f"[bold]sdlc webhook[/bold] — listening on port {port}")
    console.print(f"  Set GitHub webhook URL to: http://<your-host>:{port}/webhook")
    console.print(f"  Health check: http://localhost:{port}/health")
    if secret:
        console.print("  Signature verification: [green]enabled[/green]")
    else:
        console.print("  Signature verification: [yellow]disabled[/yellow] (set --secret or SDLC_WEBHOOK_SECRET)")
    console.print("  Press Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]webhook server stopped.[/dim]")


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

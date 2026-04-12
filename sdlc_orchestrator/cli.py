"""
sdlc CLI

  sdlc setup [source]     set up a project — new or existing
  sdlc run [--loop]       execute current phase / resume loop
  sdlc approve            approve current gate + advance
  sdlc answer [--file]    submit answers to requirement questions
  sdlc feedback <phase>   append feedback for a phase
  sdlc status             show current state
  sdlc reset [phase]      rewind to a phase
  sdlc relink             rebuild ~/.sdlc/projects/<slug> symlink

SOURCE for `sdlc setup`:
  (none)                  → new project, fully interactive
  https://github.com/…    → clone from GitHub (HTTPS)
  git@github.com:…        → clone from GitHub (SSH)
  owner/repo              → GitHub shorthand (clones via HTTPS)
  .                       → use current directory
  /path/to/repo           → use local path
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
    State, WorkflowState, STATE_LABELS, APPROVAL_STATES,
)
from sdlc_orchestrator.memory import MemoryManager, GLOBAL_MEMORY_PATH
from sdlc_orchestrator.utils import (
    create_symlink, find_project_dir, project_slug, sdlc_home, update_gitignore,
)

console = Console()

# ── source detection ──────────────────────────────────────────────────────────

def _detect_source(source: str | None) -> str:
    """Return one of: new | github | local"""
    if not source:
        return "new"
    if source.startswith(("http://", "https://", "git@")):
        return "github"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
        return "github"   # owner/repo shorthand
    return "local"


def _github_to_clone_url(source: str) -> str:
    if source.startswith(("http", "git@")):
        return source
    return f"https://github.com/{source}.git"


def _repo_name_from_source(source: str) -> str:
    return source.rstrip("/").split("/")[-1].removesuffix(".git")


def _detect_remote_repo(project_dir: Path) -> str:
    """Extract owner/repo from git remote origin."""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_dir, capture_output=True, text=True,
        )
        url = r.stdout.strip()
        m = re.search(r"[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", url)
        return m.group(1) if m else ""
    except Exception:
        return ""

# ── shared helpers ────────────────────────────────────────────────────────────

def _require_project() -> Path:
    d = find_project_dir()
    if not d:
        console.print("[red]No SDLC project found. Run [bold]sdlc setup[/bold] first.[/red]")
        sys.exit(1)
    return d


def _ensure_global_memory() -> None:
    GLOBAL_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_MEMORY_PATH.exists():
        from importlib.resources import files as _files
        try:
            content = (_files("sdlc_orchestrator") / "templates" / "global.md").read_text()
            GLOBAL_MEMORY_PATH.write_text(content)
        except Exception:
            GLOBAL_MEMORY_PATH.write_text(
                "# Global Rules\n\n"
                "- Write clean, modular code\n"
                "- Write tests for all features\n"
                "- No secrets in code — use environment variables\n"
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


def _detect_starting_phase(project_dir: Path) -> str:
    """Auto-detect starting phase from repo state, fallback to prompt."""
    has_tests = any(project_dir.rglob("test_*.py")) or any(project_dir.rglob("*.test.*"))
    has_src = any(project_dir.iterdir()) if project_dir.exists() else False

    choices = ["requirement", "design", "planning", "implementation", "validation", "review"]

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


def _run_analyze_skill(project_dir: Path, spec: dict) -> None:
    """Run sdlc-analyze-repo skill to build .sdlc/memory/project.md."""
    skill_path = Path.home() / ".claude" / "commands" / "sdlc-analyze-repo.md"
    if not skill_path.exists():
        _install_global_skills()

    prompt = skill_path.read_text() if skill_path.exists() else (
        "Analyze this repository. Write a project context summary to "
        ".sdlc/memory/project.md covering stack, architecture, domain concepts, "
        "testing setup, tech debt, and deployment. "
        "Output: PHASE_COMPLETE: analyze-repo"
    )
    prompt = prompt.replace("{{PROJECT_NAME}}", spec.get("project_name", ""))

    from sdlc_orchestrator.executor import ClaudeCodeExecutor
    result = ClaudeCodeExecutor().run(prompt, project_dir)
    if result.success:
        console.print("  [green]✓[/green] Repo analysis complete")
    else:
        console.print("  [yellow]Repo analysis had warnings — edit .sdlc/memory/project.md[/yellow]")


def _open_in_editor(path: Path) -> None:
    import os
    editor = os.environ.get("EDITOR", "")
    if editor:
        subprocess.run([editor, str(path)])
    else:
        console.print(f"  [dim]Edit manually: {path}[/dim]")


# ── common setup block (runs for all source types) ───────────────────────────

def _common_setup(project_dir: Path, spec: dict, is_new: bool, upgrade_skills: bool) -> None:
    console.print()

    # 1. Ensure global memory exists
    _ensure_global_memory()

    # 2. Create .sdlc/ dirs
    _init_sdlc_dirs(project_dir)

    # 3. Create ~/.sdlc/projects/<slug> symlink
    link = create_symlink(project_dir)
    console.print(f"  [green]✓[/green] symlink: ~/.sdlc/projects/{project_slug(project_dir)} → .sdlc/")

    # 4. Write spec.yaml into .sdlc/
    mem = MemoryManager(project_dir)
    mem.write_spec(spec)
    console.print("  [green]✓[/green] .sdlc/spec.yaml")

    # 5. Project memory
    if not mem.project_path.exists():
        from importlib.resources import files as _files
        try:
            tpl = (_files("sdlc_orchestrator") / "templates" / "project.md").read_text()
            tpl = tpl.replace("{{PROJECT_NAME}}", spec.get("project_name", "")) \
                     .replace("{{STACK}}", spec.get("tech_stack", ""))
        except Exception:
            tpl = f"# Project: {spec.get('project_name', '')}\n\nStack: {spec.get('tech_stack', '')}\n"
        mem.write_project_memory(tpl)

    # 6. If existing codebase — run analyze-repo skill
    if not is_new:
        console.print("  Running repo analysis (Claude) ...")
        _run_analyze_skill(project_dir, spec)

        project_md = mem.project_path
        if project_md.exists():
            console.print(f"  Review [bold].sdlc/memory/project.md[/bold] then press Enter.")
            _open_in_editor(project_md)
            click.pause("  Press Enter when done ...")
            mem.regenerate_claude_md()

    mem.regenerate_claude_md()
    console.print("  [green]✓[/green] CLAUDE.md (generated)")

    # 7. Workflow state
    wf = WorkflowState(project_dir)
    wf.save()
    console.print("  [green]✓[/green] .sdlc/workflow/state.json")

    # 8. Hooks
    _write_hooks(project_dir)
    console.print("  [green]✓[/green] .claude/settings.json (hooks)")

    # 9. Global skills
    _install_global_skills(force=upgrade_skills)
    console.print("  [green]✓[/green] global skills → ~/.claude/commands/")

    # 10. .gitignore
    update_gitignore(project_dir)
    console.print("  [green]✓[/green] .gitignore updated")

    # 11. GitHub board
    _setup_github_board(project_dir, spec)

    console.print(f"\n[bold green]Ready.[/bold green]")
    console.print(f"  cd {project_dir}")
    console.print(f"  sdlc run\n")


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


# ── sdlc setup ────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Autonomous SDLC orchestrator powered by Claude Code."""
    pass


@cli.command()
@click.argument("source", required=False)
@click.option("--upgrade-skills", is_flag=True, help="Re-install global skills even if present")
def setup(source, upgrade_skills):
    """Set up a project for SDLC orchestration.

    \b
    SOURCE examples:
      (none)                  interactive new project
      https://github.com/…    clone from GitHub
      owner/repo              GitHub shorthand
      .                       use current directory
      /path/to/repo           use local path
    """
    kind = _detect_source(source)

    if kind == "new":
        _setup_new(upgrade_skills)
    elif kind == "github":
        _setup_github(source, upgrade_skills)
    else:
        _setup_local(Path(source).resolve(), upgrade_skills)


def _setup_new(upgrade_skills: bool) -> None:
    console.print(Panel("[bold]New project setup[/bold]", style="blue"))

    name        = click.prompt("Project name")
    description = click.prompt("Description")
    stack       = click.prompt("Tech stack (e.g. Python/FastAPI, Node.js, React+Node)")
    repo        = click.prompt("GitHub repo owner/name (or Enter to skip)", default="")
    slack       = click.prompt("Slack webhook URL (or Enter to skip)", default="", hide_input=True)

    slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    project_dir = Path.cwd() / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    # git init
    if not (project_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=project_dir)

    # GitHub repo creation
    if repo and _gh_available():
        result = subprocess.run(
            ["gh", "repo", "create", repo, "--public", "--source", str(project_dir), "--push"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            console.print(f"  [green]✓[/green] GitHub repo created: {repo}")
        else:
            console.print(f"  [yellow]gh repo create failed — create it manually[/yellow]")

    spec = {
        "project_name": name,
        "description": description,
        "tech_stack": stack,
        "repo": repo,
        "slack_webhook": slack,
        "executor": "claude-code",
    }

    _common_setup(project_dir, spec, is_new=True, upgrade_skills=upgrade_skills)


def _setup_github(source: str, upgrade_skills: bool) -> None:
    clone_url = _github_to_clone_url(source)
    repo_name = _repo_name_from_source(source)
    project_dir = Path.cwd() / repo_name

    console.print(Panel(f"[bold]Attaching GitHub repo:[/bold] {source}", style="blue"))

    if not project_dir.exists():
        console.print(f"  Cloning {clone_url} ...")
        r = subprocess.run(["git", "clone", clone_url, str(project_dir)])
        if r.returncode != 0:
            console.print("[red]git clone failed[/red]")
            sys.exit(1)
    else:
        console.print(f"  [dim]Directory exists — skipping clone[/dim]")

    _setup_local(project_dir, upgrade_skills)


def _setup_local(project_dir: Path, upgrade_skills: bool) -> None:
    if not project_dir.exists():
        console.print(f"[red]Path does not exist: {project_dir}[/red]")
        sys.exit(1)

    is_empty = not any(f for f in project_dir.iterdir() if f.name not in {".git", ".sdlc"})
    is_new = is_empty

    console.print(Panel(
        f"[bold]{'New' if is_new else 'Existing'} project:[/bold] {project_dir.name}",
        style="blue"
    ))

    # Try to read existing spec
    existing_spec_path = project_dir / ".sdlc" / "spec.yaml"
    legacy_spec_path   = project_dir / "spec.yaml"

    if existing_spec_path.exists():
        spec = yaml.safe_load(existing_spec_path.read_text()) or {}
        console.print("  [dim]Existing .sdlc/spec.yaml found[/dim]")
    elif legacy_spec_path.exists():
        spec = yaml.safe_load(legacy_spec_path.read_text()) or {}
        console.print("  [dim]Legacy spec.yaml found — migrating to .sdlc/[/dim]")
    else:
        # Prompt for details
        default_name  = project_dir.name
        default_stack = _detect_stack(project_dir)
        default_repo  = _detect_remote_repo(project_dir)

        name        = click.prompt("Project name", default=default_name)
        stack       = click.prompt("Tech stack", default=default_stack or "")
        repo        = click.prompt("GitHub repo owner/name", default=default_repo)
        objective   = click.prompt(
            "Objective (what work are we doing on this project?)"
            if not is_new else "Description"
        )
        slack       = click.prompt("Slack webhook URL (or Enter to skip)", default="", hide_input=True)

        spec = {
            "project_name": name,
            "tech_stack": stack,
            "repo": repo,
            "executor": "claude-code",
            "slack_webhook": slack,
        }
        if is_new:
            spec["description"] = objective
        else:
            spec["objective"] = objective

    # Determine starting phase
    start_phase = _detect_starting_phase(project_dir)
    _set_initial_state(project_dir, start_phase)

    _common_setup(project_dir, spec, is_new=is_new, upgrade_skills=upgrade_skills)


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


def _detect_stack(project_dir: Path) -> str:
    if (project_dir / "package.json").exists():
        return "Node.js"
    if (project_dir / "requirements.txt").exists() or (project_dir / "pyproject.toml").exists():
        return "Python"
    if (project_dir / "go.mod").exists():
        return "Go"
    if (project_dir / "Cargo.toml").exists():
        return "Rust"
    if (project_dir / "pom.xml").exists():
        return "Java/Maven"
    return ""


def _gh_available() -> bool:
    try:
        subprocess.run(["gh", "auth", "status"], capture_output=True, check=False)
        return True
    except FileNotFoundError:
        return False


# ── sdlc run ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--loop", is_flag=True, help="Run all phases until blocked or done")
def run(loop):
    """Execute the current SDLC phase (or loop through all phases)."""
    project_dir = _require_project()
    from sdlc_orchestrator.runner import Orchestrator
    orch = Orchestrator(project_dir)
    if loop:
        orch.run_loop()
    else:
        orch.run_once()


# ── sdlc approve ─────────────────────────────────────────────────────────────

@cli.command()
def approve():
    """Approve the current human gate and advance to the next state."""
    project_dir = _require_project()
    wf = WorkflowState(project_dir)
    current = wf.state

    if current not in APPROVAL_STATES:
        console.print(f"[yellow]'{current.value}' is not an approval gate.[/yellow]")
        console.print(f"Status: {STATE_LABELS.get(current, current.value)}")
        return

    NEXT: dict[State, State] = {
        State.AWAITING_REQUIREMENT_ANSWER:     State.REQUIREMENT_IN_PROGRESS,
        State.REQUIREMENT_READY_FOR_APPROVAL:  State.DESIGN_IN_PROGRESS,
        State.AWAITING_DESIGN_APPROVAL:        State.TASK_PLAN_IN_PROGRESS,
        State.TASK_PLAN_READY:                 State.IMPLEMENTATION_IN_PROGRESS,
        State.AWAITING_REVIEW:                 State.DONE,
        State.BLOCKED:                         State.DRAFT_REQUIREMENT,
    }

    next_state = NEXT[current]
    wf.transition(next_state)
    console.print(f"[green]✓ Approved.[/green] → {STATE_LABELS.get(next_state, next_state.value)}")
    console.print("  Run [bold]sdlc run[/bold] to continue.")


# ── sdlc answer ───────────────────────────────────────────────────────────────

@cli.command()
@click.option("--file", "from_file", is_flag=True,
              help="Answers already written in the questions file — skip prompts")
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
        console.print("[red]requirement_questions.md not found. Run sdlc run first.[/red]")
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
        questions_file.write_text(
            "# Requirement Questions & Answers\n\n" + "\n".join(answers)
        )
        console.print("[green]✓ Answers saved.[/green]")

    wf.transition(State.REQUIREMENT_IN_PROGRESS)
    console.print("State → requirement_in_progress")
    console.print("Run [bold]sdlc run[/bold] to build structured requirements.")


# ── sdlc feedback ─────────────────────────────────────────────────────────────

@cli.command()
@click.argument("phase")
@click.argument("text", required=False)
def feedback(phase, text):
    """Append feedback for a phase (queued for next run)."""
    project_dir = _require_project()
    mem = MemoryManager(project_dir)
    if not text:
        text = click.edit(f"# Feedback for {phase}\n\n")
        if not text or not text.strip():
            console.print("[yellow]No feedback entered.[/yellow]")
            return
    mem.append_feedback(phase, text)
    console.print(f"[green]✓ Feedback queued for: {phase}[/green]")


# ── sdlc status ───────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show current SDLC state."""
    project_dir = _require_project()
    wf = WorkflowState(project_dir)
    spec = MemoryManager(project_dir).spec()

    table = Table(
        title=f"SDLC — {spec.get('project_name', project_dir.name)}",
        show_header=False,
    )
    table.add_column("Key", style="dim", width=20)
    table.add_column("Value")

    color = ("yellow" if wf.state in APPROVAL_STATES
             else "green" if wf.state == State.DONE else "blue")
    table.add_row("State", f"[{color}]{wf.state.value}[/{color}]")
    table.add_row("", f"[dim]{STATE_LABELS.get(wf.state, '')}[/dim]")
    table.add_row("Approval needed", "YES — sdlc approve" if wf.approval_needed else "no")
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


# ── sdlc reset ────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("phase", default="requirement")
def reset(phase):
    """Rewind the orchestrator to a given phase."""
    project_dir = _require_project()
    _set_initial_state(project_dir, phase)
    console.print(f"[green]✓ Reset to: {phase}[/green]")


# ── sdlc relink ───────────────────────────────────────────────────────────────

@cli.command()
@click.option("--all", "relink_all", is_flag=True, help="Relink all projects under ~/.sdlc/projects/")
def relink(relink_all):
    """Rebuild the ~/.sdlc/projects/<slug> symlink for this project."""
    if relink_all:
        projects_dir = Path.home() / ".sdlc" / "projects"
        if not projects_dir.exists():
            console.print("[yellow]No projects dir found.[/yellow]")
            return
        for link in projects_dir.iterdir():
            if link.is_symlink() and not link.exists():
                console.print(f"  [red]broken:[/red] {link.name} → {link.readlink()}")
            elif link.is_symlink():
                console.print(f"  [green]ok:[/green] {link.name} → {link.resolve()}")
        return

    project_dir = _require_project()
    link = create_symlink(project_dir)
    console.print(f"[green]✓ Relinked:[/green] ~/.sdlc/projects/{project_slug(project_dir)} → {link.resolve()}")


# ── legacy aliases (hidden) ───────────────────────────────────────────────────

@cli.command(hidden=True, deprecated=True)
@click.pass_context
def init(ctx):
    """Deprecated — use `sdlc setup` instead."""
    ctx.invoke(setup)


@cli.command(hidden=True, deprecated=True)
@click.argument("github_url")
@click.pass_context
def attach(ctx, github_url):
    """Deprecated — use `sdlc setup <url>` instead."""
    ctx.invoke(setup, source=github_url)

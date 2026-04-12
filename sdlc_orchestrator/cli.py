"""
sdlc CLI — entry point for all user-facing commands.

  sdlc init               new project setup
  sdlc attach <url>       onboard existing GitHub repo
  sdlc run [--loop]       execute current phase / resume loop
  sdlc approve [phase]    approve current gate + advance
  sdlc answer [--file]    submit answers to requirement questions
  sdlc feedback <phase>   append feedback for a phase
  sdlc status             show current state
  sdlc reset [phase]      rewind to a phase
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from sdlc_orchestrator.state_machine import (
    State, WorkflowState, STATE_LABELS, APPROVAL_STATES
)
from sdlc_orchestrator.memory import MemoryManager, GLOBAL_MEMORY_PATH

console = Console()

# ── helpers ───────────────────────────────────────────────────────────────────

def _project_dir() -> Path:
    """Find project root by walking up looking for spec.yaml."""
    p = Path.cwd()
    for candidate in [p, *p.parents]:
        if (candidate / "spec.yaml").exists():
            return candidate
    return p  # fallback: cwd


def _require_project() -> Path:
    d = _project_dir()
    if not (d / "spec.yaml").exists():
        console.print("[red]No spec.yaml found. Run [bold]sdlc init[/bold] first.[/red]")
        sys.exit(1)
    return d


def _install_global_skills(force: bool = False) -> None:
    """Copy bundled skill templates to ~/.claude/commands/."""
    dest_dir = Path.home() / ".claude" / "commands"
    dest_dir.mkdir(parents=True, exist_ok=True)

    from importlib.resources import files as _files
    try:
        skills_pkg = _files("sdlc_orchestrator") / "skills"
        for skill_file in skills_pkg.iterdir():  # type: ignore[attr-defined]
            name = skill_file.name
            dest = dest_dir / name
            if not dest.exists() or force:
                dest.write_text(skill_file.read_text(encoding="utf-8"))
                console.print(f"  [dim]Installed skill:[/dim] ~/.claude/commands/{name}")
    except Exception as e:
        console.print(f"[yellow]Skill install warning: {e}[/yellow]")


def _write_hooks(project_dir: Path) -> None:
    """Write .claude/settings.json with all SDLC hooks."""
    import json
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "command": "python -m sdlc_orchestrator.hooks.on_bash",
                    "timeout": 10,
                }
            ],
            "Stop": [
                {
                    "command": "python -m sdlc_orchestrator.hooks.on_stop",
                    "timeout": 30,
                }
            ],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings, indent=2))


def _write_initial_state(project_dir: Path) -> None:
    wf_dir = project_dir / "workflow"
    wf_dir.mkdir(exist_ok=True)
    (wf_dir / "artifacts").mkdir(exist_ok=True)
    (wf_dir / "logs").mkdir(exist_ok=True)
    (project_dir / "feedback").mkdir(exist_ok=True)

    state = WorkflowState(project_dir)
    # state.json auto-created with defaults by WorkflowState
    state.save()


def _ensure_global_memory() -> None:
    """Create global.md if it doesn't exist."""
    GLOBAL_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_MEMORY_PATH.exists():
        from importlib.resources import files as _files
        try:
            content = (_files("sdlc_orchestrator") / "templates" / "global.md").read_text()
            GLOBAL_MEMORY_PATH.write_text(content)
        except Exception:
            GLOBAL_MEMORY_PATH.write_text("# Global Rules\n\n- Write clean, modular code\n- Write tests for all features\n")


# ── CLI group ─────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """Autonomous SDLC orchestrator powered by Claude Code."""
    pass


# ── sdlc init ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--name",       prompt="Project name")
@click.option("--description",prompt="Short description")
@click.option("--stack",      prompt="Tech stack (e.g. Node.js, Python/FastAPI)")
@click.option("--repo",       prompt="GitHub repo (owner/name, or press Enter to skip)", default="")
@click.option("--slack",      prompt="Slack webhook URL (or press Enter to skip)", default="", hide_input=True)
@click.option("--dir",        default=".", help="Where to create the project")
@click.option("--upgrade-skills", is_flag=True, help="Re-install global skills even if present")
def init(name, description, stack, repo, slack, dir, upgrade_skills):
    """Create a new SDLC-managed project."""
    project_dir = Path(dir).resolve() / name.lower().replace(" ", "-")
    project_dir.mkdir(parents=True, exist_ok=True)

    console.print(Panel(f"[bold]Initialising:[/bold] {name}", style="blue"))

    # 1. Write spec.yaml
    spec = {
        "project_name": name,
        "description": description,
        "tech_stack": stack,
        "repo": repo,
        "slack_webhook": slack,
        "executor": "claude-code",
    }
    (project_dir / "spec.yaml").write_text(yaml.dump(spec, default_flow_style=False))
    console.print("  [green]✓[/green] spec.yaml")

    # 2. Memory layer
    _ensure_global_memory()
    mem = MemoryManager(project_dir)
    from importlib.resources import files as _files
    try:
        project_tpl = (_files("sdlc_orchestrator") / "templates" / "project.md").read_text()
        project_tpl = project_tpl.replace("{{PROJECT_NAME}}", name).replace("{{STACK}}", stack)
        mem.write_project_memory(project_tpl)
    except Exception:
        mem.write_project_memory(f"# Project: {name}\n\nStack: {stack}\n\nDescription: {description}\n")
    console.print("  [green]✓[/green] memory/project.md + CLAUDE.md")

    # 3. Workflow state
    _write_initial_state(project_dir)
    console.print("  [green]✓[/green] workflow/state.json")

    # 4. Hooks
    _write_hooks(project_dir)
    console.print("  [green]✓[/green] .claude/settings.json (hooks)")

    # 5. Global skills
    _install_global_skills(force=upgrade_skills)
    console.print("  [green]✓[/green] global skills → ~/.claude/commands/")

    # 6. Git init
    if not (project_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=project_dir)
        subprocess.run(["git", "add", "-A"], cwd=project_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: init sdlc project"], cwd=project_dir, capture_output=True)
    console.print("  [green]✓[/green] git init")

    # 7. GitHub repo + board
    if repo:
        _setup_github(project_dir, repo, name)

    console.print(f"\n[bold green]Ready.[/bold green] Run:\n\n  cd {project_dir} && sdlc run")


def _setup_github(project_dir: Path, repo: str, name: str) -> None:
    from sdlc_orchestrator.integrations import github
    if not github.is_available():
        console.print("  [yellow]gh not available — skipping GitHub setup[/yellow]")
        return
    project_id = github.create_project_board(name, repo)
    if project_id:
        wf = WorkflowState(project_dir)
        wf.set_github(project_id=project_id)
        console.print(f"  [green]✓[/green] GitHub project board created (id: {project_id})")
    else:
        console.print("  [yellow]Could not create GitHub board (check gh auth)[/yellow]")


# ── sdlc attach ───────────────────────────────────────────────────────────────

@cli.command()
@click.argument("github_url")
@click.option("--phase", default="", help="Force start from this phase (skips auto-detect)")
@click.option("--upgrade-skills", is_flag=True)
def attach(github_url, phase, upgrade_skills):
    """Onboard an existing GitHub repository."""
    # Derive repo name from URL
    repo_name = github_url.rstrip("/").split("/")[-1].removesuffix(".git")
    project_dir = Path.cwd() / repo_name

    console.print(Panel(f"[bold]Attaching:[/bold] {github_url}", style="blue"))

    # 1. Clone
    if not project_dir.exists():
        console.print("  Cloning repository ...")
        result = subprocess.run(["git", "clone", github_url, str(project_dir)])
        if result.returncode != 0:
            console.print("[red]git clone failed[/red]")
            sys.exit(1)
    else:
        console.print(f"  [dim]Directory exists, skipping clone[/dim]")

    # 2. Detect existing spec.yaml or create one
    if (project_dir / "spec.yaml").exists():
        spec = yaml.safe_load((project_dir / "spec.yaml").read_text()) or {}
        console.print("  [green]✓[/green] Existing spec.yaml found")
    else:
        name = click.prompt("Project name", default=repo_name)
        stack = click.prompt("Tech stack")
        slack = click.prompt("Slack webhook URL (optional)", default="")
        spec = {
            "project_name": name,
            "tech_stack": stack,
            "repo": github_url.replace("https://github.com/", "").removesuffix(".git"),
            "slack_webhook": slack,
            "executor": "claude-code",
        }
        (project_dir / "spec.yaml").write_text(yaml.dump(spec, default_flow_style=False))

    # 3. Run sdlc-analyze-repo skill via Claude
    console.print("  Running repo analysis via Claude ...")
    _run_analyze_skill(project_dir)

    # 4. Open project.md for review
    project_md = project_dir / "memory" / "project.md"
    if project_md.exists():
        console.print(f"\n  [bold]Review memory/project.md[/bold] (auto-generated)")
        console.print("  Edit it now, then press Enter to continue.")
        _open_in_editor(project_md)
        click.pause("  Press Enter when done reviewing ...")

    # Regenerate CLAUDE.md after edits
    mem = MemoryManager(project_dir)
    _ensure_global_memory()
    mem.regenerate_claude_md()
    console.print("  [green]✓[/green] CLAUDE.md regenerated")

    # 5. Determine starting phase
    start_phase = phase or _detect_starting_phase(project_dir)
    console.print(f"  Starting from phase: [bold]{start_phase}[/bold]")

    # 6. Write workflow state
    _write_initial_state(project_dir)
    wf = WorkflowState(project_dir)
    phase_state_map = {
        "requirement": State.DRAFT_REQUIREMENT,
        "design":      State.DESIGN_IN_PROGRESS,
        "planning":    State.TASK_PLAN_IN_PROGRESS,
        "implementation": State.IMPLEMENTATION_IN_PROGRESS,
        "validation":  State.TEST_FAILURE_LOOP,
        "review":      State.AWAITING_REVIEW,
    }
    initial = phase_state_map.get(start_phase, State.DRAFT_REQUIREMENT)
    wf._data["state"] = initial.value
    wf.save()

    # 7. Hooks + skills
    _write_hooks(project_dir)
    _install_global_skills(force=upgrade_skills)
    _setup_github(project_dir, spec.get("repo", ""), spec.get("project_name", repo_name))

    console.print(f"\n[bold green]Ready.[/bold green] cd {repo_name} && sdlc run")


def _run_analyze_skill(project_dir: Path) -> None:
    """Run sdlc-analyze-repo skill to build memory/project.md."""
    from sdlc_orchestrator.memory import MemoryManager
    mem = MemoryManager(project_dir)
    _ensure_global_memory()

    skill_path = Path.home() / ".claude" / "commands" / "sdlc-analyze-repo.md"
    if not skill_path.exists():
        _install_global_skills()

    prompt = skill_path.read_text() if skill_path.exists() else _default_analyze_prompt()
    spec = yaml.safe_load((project_dir / "spec.yaml").read_text()) or {}
    prompt = prompt.replace("{{PROJECT_NAME}}", spec.get("project_name", ""))

    try:
        from sdlc_orchestrator.executor import executor_from_spec
        executor = executor_from_spec(project_dir)
        result = executor.run(prompt, project_dir)
        if result.success:
            console.print("  [green]✓[/green] Repo analysis complete")
        else:
            console.print("  [yellow]Repo analysis had warnings — check memory/project.md[/yellow]")
    except Exception as e:
        console.print(f"  [yellow]Repo analysis skipped: {e}[/yellow]")
        # Write a basic project.md so the user has something to edit
        (project_dir / "memory").mkdir(exist_ok=True)
        (project_dir / "memory" / "project.md").write_text(
            f"# Project Context\n\n(Auto-analysis failed — fill in manually)\n\n"
            f"## Stack\n{spec.get('tech_stack', 'Unknown')}\n\n"
            f"## Notes\n\n"
        )


def _default_analyze_prompt() -> str:
    return """Analyze this repository and produce memory/project.md.

Include:
- Tech stack and versions (from package.json / requirements.txt / etc.)
- Folder structure and module responsibilities
- Existing architecture patterns
- Key domain concepts and terminology
- Existing test setup
- Known tech debt or issues (from open GitHub issues, TODO comments)
- Deployment assumptions

Write the output to memory/project.md.
When done, output: PHASE_COMPLETE: analyze-repo
"""


def _detect_starting_phase(project_dir: Path) -> str:
    choices = ["requirement", "design", "planning", "implementation", "validation", "review"]
    console.print("\n  Which phase should we start from?")
    for i, c in enumerate(choices, 1):
        console.print(f"    {i}. {c}")
    choice = click.prompt("  Enter number", default="1")
    try:
        idx = int(choice) - 1
        return choices[idx] if 0 <= idx < len(choices) else "requirement"
    except ValueError:
        return "requirement"


def _open_in_editor(path: Path) -> None:
    import os
    editor = os.environ.get("EDITOR", "")
    if editor:
        subprocess.run([editor, str(path)])


# ── sdlc run ──────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--loop", is_flag=True, help="Run all phases continuously until blocked")
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
@click.argument("phase", required=False)
def approve(phase):
    """Approve the current human gate and advance to the next state."""
    project_dir = _require_project()
    wf = WorkflowState(project_dir)
    current = wf.state

    if current not in APPROVAL_STATES:
        console.print(f"[yellow]State '{current.value}' is not an approval gate.[/yellow]")
        console.print(f"Current state: {STATE_LABELS.get(current, current.value)}")
        return

    NEXT_STATE: dict[State, State] = {
        State.AWAITING_REQUIREMENT_ANSWER:     State.REQUIREMENT_IN_PROGRESS,
        State.REQUIREMENT_READY_FOR_APPROVAL:  State.DESIGN_IN_PROGRESS,
        State.AWAITING_DESIGN_APPROVAL:        State.TASK_PLAN_IN_PROGRESS,
        State.TASK_PLAN_READY:                 State.IMPLEMENTATION_IN_PROGRESS,
        State.AWAITING_REVIEW:                 State.DONE,
        State.BLOCKED:                         State.DRAFT_REQUIREMENT,
    }

    next_state = NEXT_STATE.get(current)
    if not next_state:
        console.print(f"[red]No transition defined from {current.value}[/red]")
        return

    wf.transition(next_state)
    console.print(f"[green]✓ Approved.[/green] → {STATE_LABELS.get(next_state, next_state.value)}")
    console.print("  Run [bold]sdlc run[/bold] to continue.")


# ── sdlc answer ───────────────────────────────────────────────────────────────

@cli.command()
@click.option("--file", "from_file", is_flag=True,
              help="Skip prompts — answers already written in the questions file")
def answer(from_file):
    """Submit answers to requirement clarifying questions."""
    project_dir = _require_project()
    wf = WorkflowState(project_dir)

    if wf.state != State.AWAITING_REQUIREMENT_ANSWER:
        console.print(f"[yellow]Not awaiting answers. Current state: {wf.state.value}[/yellow]")
        return

    questions_file = project_dir / "workflow" / "artifacts" / "requirement_questions.md"
    if not questions_file.exists():
        console.print("[red]requirement_questions.md not found. Run sdlc run first.[/red]")
        return

    if from_file:
        console.print(f"[dim]Using answers already written in {questions_file}[/dim]")
    else:
        # Terminal interactive: display questions, collect answers inline
        content = questions_file.read_text()
        lines = content.splitlines()
        answers: list[str] = []

        console.print(Panel("[bold]Requirement Clarifying Questions[/bold]\nType your answer after each question.", style="blue"))

        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("## Q"):
                console.print(f"\n[bold]{line}[/bold]")
                # Print lines until Answer: field
                i += 1
                while i < len(lines) and not lines[i].startswith("**Answer:**") and not lines[i].startswith("## Q"):
                    console.print(f"  {lines[i]}")
                    i += 1
                ans = click.prompt("  Your answer")
                answers.append(f"\n{line}\n**Answer:** {ans}\n")
            else:
                i += 1

        # Rewrite questions file with answers embedded
        questions_file.write_text(
            "# Requirement Clarifying Questions & Answers\n\n" +
            "\n".join(answers)
        )
        console.print("[green]✓ Answers saved.[/green]")

    # Advance state → Claude will now build the structured requirements
    wf.transition(State.REQUIREMENT_IN_PROGRESS)
    console.print("State → requirement_in_progress")
    console.print("Run [bold]sdlc run[/bold] to build structured requirements.")


# ── sdlc feedback ─────────────────────────────────────────────────────────────

@cli.command()
@click.argument("phase")
@click.argument("text", required=False)
def feedback(phase, text):
    """Append feedback for a phase (queued for next iteration)."""
    project_dir = _require_project()
    mem = MemoryManager(project_dir)

    if not text:
        text = click.edit(f"# Feedback for {phase}\n\n")
        if not text or not text.strip():
            console.print("[yellow]No feedback entered.[/yellow]")
            return

    mem.append_feedback(phase, text)
    console.print(f"[green]✓ Feedback queued for phase: {phase}[/green]")


# ── sdlc status ───────────────────────────────────────────────────────────────

@cli.command()
def status():
    """Show current SDLC state and history."""
    project_dir = _require_project()
    wf = WorkflowState(project_dir)
    spec = (yaml.safe_load((project_dir / "spec.yaml").read_text()) or {}) if (project_dir / "spec.yaml").exists() else {}

    table = Table(title=f"SDLC Status — {spec.get('project_name', project_dir.name)}", show_header=False)
    table.add_column("Key", style="dim", width=20)
    table.add_column("Value")

    state_label = STATE_LABELS.get(wf.state, wf.state.value)
    color = "yellow" if wf.state in APPROVAL_STATES else ("green" if wf.state == State.DONE else "blue")
    table.add_row("State", f"[{color}]{wf.state.value}[/{color}]")
    table.add_row("", f"[dim]{state_label}[/dim]")
    table.add_row("Approval needed", "YES — run sdlc approve" if wf.approval_needed else "no")
    table.add_row("Branch", wf._data.get("current_branch", "main"))
    table.add_row("Last updated", wf._data.get("last_updated", "—")[:19])
    if wf.blocked_reason:
        table.add_row("Blocked reason", f"[red]{wf.blocked_reason}[/red]")

    console.print(table)

    history = wf._data.get("history", [])
    if history:
        console.print("\n[dim]Recent history:[/dim]")
        for h in history[-6:]:
            ts = h["timestamp"][:19].replace("T", " ")
            console.print(f"  {ts}  {h['state']}")


# ── sdlc reset ────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("phase", default="requirement")
def reset(phase):
    """Reset orchestrator to a given phase."""
    PHASE_STATES = {
        "requirement":    State.DRAFT_REQUIREMENT,
        "design":         State.DESIGN_IN_PROGRESS,
        "planning":       State.TASK_PLAN_IN_PROGRESS,
        "implementation": State.IMPLEMENTATION_IN_PROGRESS,
        "validation":     State.TEST_FAILURE_LOOP,
        "review":         State.AWAITING_REVIEW,
    }
    if phase not in PHASE_STATES:
        console.print(f"[red]Unknown phase: {phase}. Choose from: {', '.join(PHASE_STATES)}[/red]")
        sys.exit(1)

    project_dir = _require_project()
    wf = WorkflowState(project_dir)
    target = PHASE_STATES[phase]
    wf._data["state"] = target.value
    wf._data["approval_needed"] = False
    wf._data["blocked_reason"] = None
    wf._data["retry_count"] = 0
    wf.save()
    console.print(f"[green]✓ Reset to:[/green] {target.value}")

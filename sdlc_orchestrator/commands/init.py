from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import click
import yaml
from rich.panel import Panel

from sdlc_orchestrator.state_machine import Phase, Status, WorkflowState
from sdlc_orchestrator.memory import GLOBAL_MEMORY_PATH, MemoryManager
from sdlc_orchestrator.utils import (
    create_symlink, project_slug, sdlc_home, update_gitignore,
)
from sdlc_orchestrator.commands import console


# ── source detection ──────────────────────────────────────────────────────────

def detect_source(source: str | None) -> str:
    if not source:
        return "new"
    if source.startswith(("http://", "https://", "git@")):
        return "github"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source):
        return "github"
    return "local"


def github_to_clone_url(source: str) -> str:
    return source if source.startswith(("http", "git@")) else f"https://github.com/{source}.git"


def repo_name_from_source(source: str) -> str:
    return source.rstrip("/").split("/")[-1].removesuffix(".git")


def detect_remote_repo(project_dir: Path) -> str:
    try:
        r = subprocess.run(["git", "remote", "get-url", "origin"],
                           cwd=project_dir, capture_output=True, text=True)
        m = re.search(r"[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$", r.stdout.strip())
        return m.group(1) if m else ""
    except Exception:
        return ""


def detect_stack(project_dir: Path) -> str:
    markers = {
        "package.json": "Node.js", "requirements.txt": "Python",
        "pyproject.toml": "Python", "go.mod": "Go",
        "Cargo.toml": "Rust", "pom.xml": "Java/Maven",
    }
    for f, stack in markers.items():
        if (project_dir / f).exists():
            return stack
    return ""


# ── setup helpers ─────────────────────────────────────────────────────────────

def ensure_global_memory() -> None:
    GLOBAL_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_MEMORY_PATH.exists():
        from importlib.resources import files as _files
        try:
            content = (_files("sdlc_orchestrator") / "templates" / "global.md").read_text()
            GLOBAL_MEMORY_PATH.write_text(content)
        except Exception:
            GLOBAL_MEMORY_PATH.write_text(
                "# Global Rules\n\n- Write clean, modular code\n"
                "- Write tests for all features\n- No secrets in code\n"
            )


def trigger_agent(project_dir: Path, skill: str = "sdlc-orchestrate") -> subprocess.CompletedProcess | None:
    from sdlc_orchestrator.memory import EXECUTOR_CLI, MemoryManager, executor_config
    spec = MemoryManager(project_dir).spec()
    executor = spec.get("executor", "claude-code")
    cmd_template = EXECUTOR_CLI.get(executor, EXECUTOR_CLI["claude-code"])
    if not cmd_template:
        return None

    if executor == "codex":
        _, skills_dir, _ = executor_config(executor)
        skill_file = skills_dir / f"{skill}.md"
        prompt = skill_file.read_text() if skill_file.exists() else skill
        cmd = [part.replace("{skill}", prompt) for part in cmd_template]
    else:
        cmd = [part.replace("{skill}", skill) for part in cmd_template]

    return subprocess.run(cmd, cwd=str(project_dir))


def install_global_skills(force: bool = False, executor: str = "claude-code") -> None:
    from importlib.resources import files as _files
    from sdlc_orchestrator.memory import executor_config
    _, dest_dir, _ = executor_config(executor)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        skills_pkg = _files("sdlc_orchestrator") / "skills"
        for skill_file in skills_pkg.iterdir():  # type: ignore[attr-defined]
            if executor == "kiro":
                skill_name = skill_file.name.replace(".md", "")
                md_dest = dest_dir / skill_file.name
                if not md_dest.exists() or force:
                    md_dest.write_text(skill_file.read_text(encoding="utf-8"))
                agents_dir = Path.home() / ".kiro" / "agents"
                agents_dir.mkdir(parents=True, exist_ok=True)
                agent_json = agents_dir / f"{skill_name}.json"
                if not agent_json.exists() or force:
                    import json as _json
                    agent_json.write_text(_json.dumps({
                        "name": skill_name,
                        "description": f"SDLC skill: {skill_name}",
                        "prompt": f"file://{md_dest}",
                        "tools": ["fs_read", "fs_write", "execute_bash", "grep", "glob"],
                        "allowedTools": ["fs_read", "fs_write", "execute_bash", "grep", "glob"],
                    }, indent=2))
                console.print(f"  [dim]skill:[/dim] {md_dest}  +  {agent_json}")
            else:
                dest = dest_dir / skill_file.name
                if not dest.exists() or force:
                    dest.write_text(skill_file.read_text(encoding="utf-8"))
                    console.print(f"  [dim]skill:[/dim] {dest_dir}/{skill_file.name}")
    except Exception as e:
        console.print(f"  [yellow]Skill install warning: {e}[/yellow]")


def write_hooks(project_dir: Path, executor: str = "claude-code") -> None:
    import json
    from sdlc_orchestrator.memory import executor_config
    _, _, settings_rel = executor_config(executor)
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


def init_sdlc_dirs(project_dir: Path) -> None:
    home = sdlc_home(project_dir)
    for sub in ["memory", "workflow/artifacts", "workflow/logs", "feedback"]:
        (home / sub).mkdir(parents=True, exist_ok=True)


def set_initial_state(project_dir: Path, phase: str = "requirement") -> None:
    wf = WorkflowState(project_dir)
    try:
        p = Phase(phase)
    except ValueError:
        p = Phase.REQUIREMENT
    wf._data["phase"] = p.value
    wf._data["status"] = Status.IN_PROGRESS.value
    if p != Phase.DONE:
        wf._data.setdefault("phases", {})[p.value] = {"status": Status.IN_PROGRESS.value}
    wf.save()


def setup_github_board(project_dir: Path, spec: dict) -> None:
    from sdlc_orchestrator.integrations import github
    repo = spec.get("repo", "")
    if not repo or not github.is_available():
        return
    wf = WorkflowState(project_dir)
    if wf.github_project:
        console.print("  [dim]GitHub project board already exists — skipped[/dim]")
        return
    project_info = github.create_project_board(spec.get("project_name", ""), repo)
    if project_info:
        wf.set_github_project(project_info)
        wf.save()
        console.print(f"  [green]✓[/green] GitHub project board #{project_info['number']}")
    else:
        console.print("  [yellow]GitHub board skipped (check gh auth scope: project)[/yellow]")


def run_analyze_skill(project_dir: Path, spec: dict) -> None:
    from sdlc_orchestrator.memory import executor_config
    executor = spec.get("executor", "claude-code")
    _, skills_dir, _ = executor_config(executor)
    if not (skills_dir / "sdlc-analyze-repo.md").exists():
        install_global_skills(executor=executor)
    try:
        result = trigger_agent(project_dir, skill="sdlc-analyze-repo")
        if result is None:
            console.print("  [yellow]Repo analysis skipped (executor has no headless CLI)[/yellow]")
        elif result.returncode == 0:
            console.print("  [green]✓[/green] Repo analysis complete")
        else:
            console.print("  [yellow]Repo analysis had warnings — edit .sdlc/memory/project.md[/yellow]")
    except Exception:
        console.print("  [yellow]Repo analysis skipped (agent CLI not found)[/yellow]")


def common_setup(project_dir: Path, spec: dict, is_new: bool, upgrade_skills: bool) -> None:
    import os
    console.print()
    ensure_global_memory()
    init_sdlc_dirs(project_dir)

    slug = project_slug(project_dir)
    create_symlink(project_dir)
    console.print(f"  [green]✓[/green] symlink ~/.sdlc/projects/{slug} → {project_dir}/")

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
        run_analyze_skill(project_dir, spec)
        if mem.project_path.exists():
            console.print("  Review [bold].sdlc/memory/project.md[/bold] then press Enter.")
            editor = os.environ.get("EDITOR", "")
            if editor:
                subprocess.run([editor, str(mem.project_path)])
            else:
                console.print(f"  [dim]Edit: {mem.project_path}[/dim]")
            click.pause("  Press Enter when done ...")
            mem.regenerate_claude_md()

    mem.regenerate_claude_md()
    console.print(f"  [green]✓[/green] {mem.context_file_path.name}")

    WorkflowState(project_dir).save()
    console.print("  [green]✓[/green] .sdlc/workflow/state.json")

    from sdlc_orchestrator.memory import executor_config
    _, skills_dir, settings_rel = executor_config(executor)
    write_hooks(project_dir, executor)
    console.print(f"  [green]✓[/green] {settings_rel}/settings.json")

    install_global_skills(force=upgrade_skills, executor=executor)
    console.print(f"  [green]✓[/green] skills → {skills_dir}/  (including /sdlc-orchestrate)")

    update_gitignore(project_dir)
    console.print("  [green]✓[/green] .gitignore")

    setup_github_board(project_dir, spec)

    console.print(f"""
[bold green]Ready.[/bold green]

  cd {project_dir}

  Next steps:
    1. Open your agent ({executor}) in this directory
    2. Run [bold]/sdlc-setup[/bold] — agent interviews you and drafts requirements
    3. Review requirements, then run [bold]/sdlc-start[/bold]

  The agent drives the rest. You'll get Slack pings at each approval gate.
""")


# ── CLI command ───────────────────────────────────────────────────────────────

@click.command()
@click.argument("source", required=False)
@click.option("--upgrade-skills", is_flag=True)
@click.option("--no-approvals", is_flag=True, help="Disable all phase approval gates (agent advances automatically).")
def init(source, upgrade_skills, no_approvals):
    """Scaffold a project for SDLC orchestration.

    \b
    SOURCE:
      (none)               interactive new project
      owner/repo           clone from GitHub
      https://github.com/… clone from GitHub
      .  or  /path         use existing local directory
    """
    kind = detect_source(source)
    if kind == "new":
        _setup_new(upgrade_skills, no_approvals)
    elif kind == "github":
        project_dir = Path.cwd() / repo_name_from_source(source)
        console.print(Panel(f"[bold]Attaching GitHub repo:[/bold] {source}", style="blue"))
        if not project_dir.exists():
            r = subprocess.run(["git", "clone", github_to_clone_url(source), str(project_dir)])
            if r.returncode != 0:
                console.print("[red]git clone failed[/red]")
                sys.exit(1)
        _setup_local(project_dir, upgrade_skills, no_approvals)
    else:
        _setup_local(Path(source).resolve(), upgrade_skills, no_approvals)


def _setup_new(upgrade_skills: bool, no_approvals: bool = False) -> None:
    console.print(Panel("[bold]New project setup[/bold]", style="blue"))
    name = click.prompt("Project name (used for directory name)")
    slug = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    project_dir = Path.cwd() / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    if not (project_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=project_dir)

    spec = {
        "project_name": name, "description": "", "tech_stack": "",
        "repo": "", "slack_webhook": "", "executor": "claude-code",
    }
    if no_approvals:
        spec["phase_approvals"] = {
            "requirement": False, "design": False, "planning": False,
            "implementation": False, "testing": False, "review": False,
        }
    common_setup(project_dir, spec, is_new=True, upgrade_skills=upgrade_skills)


def _setup_local(project_dir: Path, upgrade_skills: bool, no_approvals: bool = False) -> None:
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

    spec_path = sdlc_home(project_dir) / "spec.yaml"
    if spec_path.exists():
        spec = yaml.safe_load(spec_path.read_text()) or {}
    else:
        spec = {
            "project_name": project_dir.name, "description": "",
            "tech_stack": detect_stack(project_dir),
            "repo": detect_remote_repo(project_dir),
            "slack_webhook": "", "executor": "claude-code",
        }

    if no_approvals:
        spec["phase_approvals"] = {
            "requirement": False, "design": False, "planning": False,
            "implementation": False, "testing": False, "review": False,
        }

    set_initial_state(project_dir, "requirement")
    common_setup(project_dir, spec, is_new=is_new, upgrade_skills=upgrade_skills)

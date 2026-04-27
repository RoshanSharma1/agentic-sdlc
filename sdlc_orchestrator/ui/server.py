"""Minimal FastAPI server for the Chorus dashboard."""
from __future__ import annotations

import asyncio
import json
import inspect
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from sdlc_orchestrator import __version__
from sdlc_orchestrator.agent_registry import AgentRegistry
from sdlc_orchestrator.backend import (
    get_runtime,
    get_workflow_service,
    record_approval_event,
    sync_project_from_disk,
)
from sdlc_orchestrator.commands.init import detect_remote_repo, init_sdlc_dirs, set_initial_state
from sdlc_orchestrator.integrations import github
from sdlc_orchestrator.memory import DEFAULT_EXECUTOR, EXECUTOR_CLI, MemoryManager
from sdlc_orchestrator.state_machine import Phase, Status, WorkflowState
from sdlc_orchestrator.utils import find_project_dir, get_active_project, project_slug, sdlc_home, update_gitignore

app = FastAPI(title="Chorus")

# Injected at startup
_project_dir: Path = Path(".")
_chat_dir: Path = Path(".")

_chat_started: dict[str, bool] = {}
_chat_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_jobs_dir: Path = Path.home() / ".sdlc" / "chat-jobs"
_jobs_dir.mkdir(parents=True, exist_ok=True)
_agent_status_cache_lock = threading.Lock()
_agent_status_cache_ttl_seconds = 300.0
_agent_status_cache_payload: dict[str, Any] | None = None
_agent_status_cache_updated_at = 0.0

CHAT_EXECUTOR_CONFIG: dict[str, dict[str, Any]] = {
    "claude-code": {
        "label": "Claude Code",
        "cmd": ["claude", "-p", "--dangerously-skip-permissions"],
        "resume": False,
    },
    "codex": {
        "label": "Codex",
        "cmd": ["codex", "exec", "--full-auto"],
        "resume": False,
    },
    "kiro": {
        "label": "Kiro",
        "cmd": ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools"],
        "resume": True,
    },
}


def _runtime_meta() -> dict[str, str]:
    package_file = Path(inspect.getfile(__import__("sdlc_orchestrator"))).resolve()
    package_root = package_file.parent
    ui_entry_file = (Path(__file__).parent / "react-app" / "dist" / "index.html").resolve()
    source_mode = "installed" if "site-packages" in package_root.parts else "repo"
    return {
        "version": __version__,
        "source_mode": source_mode,
        "package_root": str(package_root),
        "server_file": str(Path(__file__).resolve()),
        "ui_entry_file": str(ui_entry_file),
        "project_dir": str(_project_dir.resolve()),
    }


def _agent_status_cache_now() -> float:
    return time.monotonic()


def _job_path(job_id: str) -> Path:
    return _jobs_dir / f"{job_id}.jsonl"


def _load_job(job_id: str) -> dict | None:
    p = _job_path(job_id)
    if not p.exists():
        return None
    lines = []
    done = False
    for raw in p.read_text().splitlines():
        try:
            entry = __import__('json').loads(raw)
            if entry.get("done"):
                done = True
            elif "line" in entry:
                lines.append(entry["line"])
        except Exception:
            pass
    return {"lines": lines, "done": done}


def _append_job(job_id: str, line: str | None = None, done: bool = False):
    with open(_job_path(job_id), "a") as f:
        if line is not None:
            f.write(json.dumps({"line": line}) + "\n")
        if done:
            f.write(json.dumps({"done": True}) + "\n")


# ── Filesystem browse ─────────────────────────────────────────────────────────

@app.get("/api/fs/browse")
def fs_browse(path: str = "~", include_files: bool = False):
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        p = p.parent
    dirs = sorted([d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith('.')], key=str.lower)
    files = sorted([f.name for f in p.iterdir() if f.is_file() and not f.name.startswith('.')], key=str.lower) if include_files else []
    entries = (
        [{"name": name, "path": str(p / name), "is_dir": True, "is_file": False} for name in dirs] +
        [{"name": name, "path": str(p / name), "is_dir": False, "is_file": True} for name in files]
    )
    return {"path": str(p), "parent": str(p.parent), "dirs": dirs, "files": files, "entries": entries}


@app.get("/api/meta")
def runtime_meta():
    return _runtime_meta()


def _build_agent_status_payload() -> dict[str, Any]:
    from sdlc_orchestrator.agent_status_checker import get_agent_usage_stats

    statuses = get_agent_usage_stats(_project_dir)
    recommended = None
    for agent_name in ["claude-code", "kiro", "codex"]:
        status = statuses.get(agent_name)
        if status and status.state == "ready":
            recommended = agent_name
            break
    if recommended is None:
        for agent_name in ["claude-code", "kiro", "codex"]:
            status = statuses.get(agent_name)
            if status and status.available:
                recommended = agent_name
                break

    return {
        "agents": {
            name: {
                "name": status.name,
                "available": status.available,
                "installed": status.installed,
                "authenticated": status.authenticated,
                "exhausted": status.exhausted,
                "state": status.state,
                "next_reset_at": status.next_reset_at,
                "version": status.version,
                "credits_remaining": status.credits_remaining,
                "credits_limit": status.credits_limit,
                "subscription_tier": status.subscription_tier,
                "rate_limit_remaining": status.rate_limit_remaining,
                "auth_method": status.auth_method,
                "account_label": status.account_label,
                "status_command": status.status_command,
                "interactive_status_command": status.interactive_status_command,
                "interactive_usage_command": status.interactive_usage_command,
                "status_source": status.status_source,
                "status_details": status.status_details,
                "notes": status.notes,
                "error_message": status.error_message,
                "last_checked": status.last_checked,
                # Usage stats
                "total_api_calls": status.total_api_calls,
                "total_tokens": status.total_tokens,
                "total_cost": status.total_cost,
                "daily_usage": {
                    "api_calls": status.daily_usage.api_calls,
                    "tokens_used": status.daily_usage.tokens_used,
                    "cost_usd": status.daily_usage.cost_usd,
                    "period": "Today"
                } if status.daily_usage else None,
                "weekly_usage": {
                    "api_calls": status.weekly_usage.api_calls,
                    "tokens_used": status.weekly_usage.tokens_used,
                    "cost_usd": status.weekly_usage.cost_usd,
                    "period": "Last 7 days"
                } if status.weekly_usage else None,
                "monthly_usage": {
                    "api_calls": status.monthly_usage.api_calls,
                    "tokens_used": status.monthly_usage.tokens_used,
                    "cost_usd": status.monthly_usage.cost_usd,
                    "period": "Last 30 days"
                } if status.monthly_usage else None,
                "next_reset_date": status.next_reset_date,
                "billing_cycle": status.billing_cycle,
                "usage_windows": [
                    {
                        "label": window.label,
                        "used_percentage": window.used_percentage,
                        "remaining_percentage": window.remaining_percentage,
                        "reset_at": window.reset_at,
                        "exhausted": window.exhausted,
                    }
                    for window in status.usage_windows
                ],
            }
            for name, status in statuses.items()
        },
        "recommended_agent": recommended,
        "total_available": sum(1 for s in statuses.values() if s.available),
    }


@app.get("/api/agents/status")
def get_global_agent_status():
    """Get cached global agent status, refreshing every 5 minutes."""
    global _agent_status_cache_payload, _agent_status_cache_updated_at

    now = _agent_status_cache_now()
    with _agent_status_cache_lock:
        if (
            _agent_status_cache_payload is not None
            and now - _agent_status_cache_updated_at < _agent_status_cache_ttl_seconds
        ):
            return _agent_status_cache_payload

    payload = _build_agent_status_payload()

    with _agent_status_cache_lock:
        _agent_status_cache_payload = payload
        _agent_status_cache_updated_at = _agent_status_cache_now()
        return _agent_status_cache_payload


# ── Chat CWD ──────────────────────────────────────────────────────────────────

@app.get("/api/chat/cwd")
def get_cwd():
    return {"cwd": str(_chat_dir)}


@app.get("/api/chat/meta")
def get_chat_meta(executor: str | None = None):
    meta = _resolve_chat_executor(_chat_dir, executor)
    return {"cwd": str(_chat_dir), **meta}


@app.post("/api/chat/cwd")
def set_cwd(body: dict):
    global _chat_dir
    path = Path(body.get("path", "")).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(400, f"Not a directory: {path}")
    _chat_dir = path
    with _chat_lock:
        _chat_started.pop(str(path), None)
    return {"cwd": str(_chat_dir)}


@app.post("/api/chat/clear")
def clear_chat():
    with _chat_lock:
        _chat_started.clear()
    return {"ok": True}


# ── Chat ──────────────────────────────────────────────────────────────────────

def _global_repo_roots() -> list[Path]:
    """Discover all known repo roots from ~/.sdlc/projects/ symlinks and _project_dir."""
    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        try:
            resolved = p.resolve()
        except Exception:
            return
        if resolved not in seen and resolved.exists():
            seen.add(resolved)
            roots.append(resolved)

    _add(_project_dir)

    registry = Path.home() / ".sdlc" / "projects"
    if not registry.is_dir():
        return roots

    for link in registry.iterdir():
        if not link.is_symlink():
            continue
        # Read raw target string — works even for stale symlinks
        try:
            raw_target = Path(str(link.readlink()) if hasattr(link, "readlink") else __import__("os").readlink(str(link)))
        except Exception:
            continue

        # Canonicalise: if relative, make it absolute relative to link parent
        if not raw_target.is_absolute():
            raw_target = (link.parent / raw_target).resolve()

        target_str = str(raw_target)

        # Extract repo root by splitting on /worktree/
        if "/worktree/" in target_str:
            repo_root = Path(target_str.split("/worktree/")[0])
            _add(repo_root)
            continue

        # Fallback: try resolving live
        try:
            target = link.resolve()
        except Exception:
            continue
        if not target.exists():
            continue
        if (target / ".sdlc").is_dir():
            if target.parent.name == "worktree":
                _add(target.parent.parent)
            else:
                _add(target.parent)
        elif (target / "worktree").is_dir():
            _add(target)
        else:
            _add(target.parent)

    return roots


def _resolve_project_dir(name: str) -> Path:
    """Resolve a dashboard project slug to the worktree or archive that owns its .sdlc state."""
    record = get_runtime().store.get_project(name)
    if record:
        candidate = Path(record.project_dir)
        if candidate.exists() and _has_project_state(candidate):
            return candidate

    for root in _global_repo_roots():
        worktree_root = root / "worktree"
        if worktree_root.exists():
            for child in worktree_root.iterdir():
                if child.is_dir() and _has_project_state(child):
                    if child.name == name or get_active_project(child) == name:
                        return child
        archive = root / ".projects" / name
        if archive.is_dir() and _has_project_state(archive):
            return archive

    raise HTTPException(404, f"SDLC project not found: '{name}'")


def _has_project_state(project_dir: Path) -> bool:
    sdlc_dir = project_dir / ".sdlc"
    return (sdlc_dir / "spec.yaml").exists() or (sdlc_dir / "workflow" / "state.json").exists()



def _known_project_entries() -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for root in _global_repo_roots():
        worktree_root = root / "worktree"
        if not worktree_root.exists():
            continue
        for child in worktree_root.iterdir():
            if not child.is_dir():
                continue
            try:
                project_dir = child.resolve()
            except Exception:
                continue
            if not _has_project_state(project_dir) or project_dir in seen:
                continue
            seen.add(project_dir)
            entries.append((project_dir, get_active_project(project_dir)))
    return entries


def _known_archive_entries() -> list[tuple[Path, str]]:
    """Return (project_dir, name) for completed projects archived under .projects/."""
    entries: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for root in _global_repo_roots():
        archive_root = root / ".projects"
        if not archive_root.exists():
            continue
        for child in sorted(archive_root.iterdir()):
            if not child.is_dir():
                continue
            resolved = child.resolve()
            if resolved in seen:
                continue
            if _has_project_state(child):
                seen.add(resolved)
                entries.append((child, child.name))
    return entries


def _refresh_project_catalog() -> None:
    runtime = get_runtime()
    for project_dir, _ in _known_project_entries():
        try:
            sync_project_from_disk(project_dir)
        except Exception:
            continue
    for project_dir, _ in _known_archive_entries():
        try:
            runtime.archive_project(project_dir)
        except Exception:
            continue


def _db_project_entries() -> list[tuple[Path, str, bool]]:
    _refresh_project_catalog()
    entries: list[tuple[Path, str, bool]] = []
    seen: set[Path] = set()
    for record in get_runtime().store.list_projects():
        project_dir = Path(record.project_dir)
        if project_dir in seen or not project_dir.exists() or not _has_project_state(project_dir):
            continue
        seen.add(project_dir)
        entries.append((project_dir, record.slug, bool(record.archived_at)))
    return entries

def _build_sdlc_context() -> str:
    """Build a brief SDLC state summary to inject as context."""
    try:
        entries = [(project_dir, slug) for project_dir, slug, archived in _db_project_entries() if not archived]
        if not entries:
            return ""
        lines = ["Current SDLC project states (as of now):"]
        for wf_dir, name in entries:
            try:
                wf = WorkflowState(wf_dir)
                spec = MemoryManager(wf_dir).spec()
                display = spec.get("project_name", name)
                done = len(wf.completed_stories)
                lines.append(f"- {display} (slug: {name}): {wf.phase.value}:{wf.status.value}, completed_stories={done}, last_updated={wf._data.get('last_updated','')[:19]}")
            except Exception:
                lines.append(f"- {name}: (unreadable)")
        return "\n".join(lines)
    except Exception:
        return ""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _ui_repo_root() -> Path:
    project_dir = _project_dir.resolve()
    if project_dir.name == "worktree":
        return project_dir.parent
    if project_dir.parent.name == "worktree":
        return project_dir.parent.parent
    return project_dir


def _approval_map(enabled: bool) -> dict[str, bool]:
    phases = ["requirement", "design", "planning", "implementation", "testing", "documentation"]
    return {phase: enabled for phase in phases}


def _known_agent_order(preferred: str, requested: list[str]) -> list[str]:
    order: list[str] = []
    if preferred:
        order.append(preferred)
    for name in requested:
        if name and name not in order:
            order.append(name)
    for name in ("claude-code", "kiro", "codex", "cline"):
        if name not in order:
            order.append(name)
    return order


def _project_template(project_name: str, description: str, tech_stack: str) -> str:
    lines = [f"# Project: {project_name}"]
    if description:
        lines.extend(["", description])
    if tech_stack:
        lines.extend(["", f"Stack: {tech_stack}"])
    return "\n".join(lines) + "\n"


def _project_intake_data(name: str) -> dict[str, Any]:
    runtime = get_runtime()
    source = runtime.store.get_project_source(name)
    repo_binding = runtime.store.get_project_repo_binding(name)
    return {
        "source": (
            {
                "type": source.source_type,
                "label": source.label,
                "location": source.location,
                "content_text": source.content_text,
                "metadata": source.metadata,
                "updated_at": source.updated_at,
            }
            if source
            else None
        ),
        "repo_binding": (
            {
                "provider": repo_binding.provider,
                "mode": repo_binding.mode,
                "repo_name": repo_binding.repo_name,
                "repo_url": repo_binding.repo_url,
                "local_path": repo_binding.local_path,
                "is_new": repo_binding.is_new,
                "metadata": repo_binding.metadata,
                "updated_at": repo_binding.updated_at,
            }
            if repo_binding
            else None
        ),
    }


def _source_content_from_body(body: dict[str, Any]) -> str:
    content = str(body.get("source_text") or body.get("idea") or "").strip()
    source_path = str(body.get("source_path") or "").strip()
    if content or not source_path:
        return content
    try:
        path = Path(source_path).expanduser().resolve()
    except Exception:
        return ""
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text()[:200000]
    except Exception:
        return ""


def _repo_binding_from_body(body: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    repo_mode = str(body.get("repo_mode") or "").strip() or ("existing" if body.get("repo") else "detected")
    provider = str(body.get("repo_provider") or ("github" if repo_mode in {"existing", "new"} else "local")).strip()
    repo_value = str(body.get("repo") or "").strip()
    repo_url = str(body.get("repo_url") or "").strip()

    if repo_mode == "new":
        repo_name = str(body.get("repo_name") or "").strip()
        if not repo_name:
            raise HTTPException(400, "repo_name is required when repo_mode is 'new'")
        created = github.create_repository(
            repo_name,
            description=str(body.get("description") or "").strip(),
            private=bool(body.get("repo_private", True)),
        )
        if not created:
            raise HTTPException(400, f"Failed to create repository '{repo_name}'")
        repo_value = created["full_name"]
        repo_url = created["url"]
    elif repo_mode == "existing":
        if not repo_value:
            raise HTTPException(400, "repo is required when repo_mode is 'existing'")
        if not repo_url and repo_value:
            repo_url = f"https://github.com/{_repo_slug(repo_value)}"
    elif repo_mode == "detected" and not repo_value:
        repo_value = detect_remote_repo(repo_root)
        if repo_value and not repo_url:
            repo_url = f"https://github.com/{_repo_slug(repo_value)}"

    return {
        "provider": provider,
        "mode": repo_mode,
        "repo_name": _repo_slug(repo_value) or repo_value,
        "repo_url": repo_url,
        "is_new": repo_mode == "new",
        "metadata": {
            "repo_private": bool(body.get("repo_private", True)),
            "requested_repo": str(body.get("repo") or "").strip(),
        },
    }


def _run_git(repo_root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise HTTPException(400, (result.stderr or result.stdout or "git command failed").strip())
    return result


def _ensure_worktree(repo_root: Path, slug: str) -> tuple[Path, str]:
    _run_git(repo_root, ["rev-parse", "--show-toplevel"])
    branch = f"worktree/{slug}"
    worktree_path = repo_root / "worktree" / slug

    base_ref = "main"
    if _run_git(repo_root, ["rev-parse", "--verify", "main"], check=False).returncode != 0:
        base_ref = "HEAD"

    if _run_git(repo_root, ["rev-parse", "--verify", branch], check=False).returncode != 0:
        _run_git(repo_root, ["branch", branch, base_ref])

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if not worktree_path.exists():
        _run_git(repo_root, ["worktree", "add", str(worktree_path), branch])
    return worktree_path, branch


def _agent_registry_data(project_dir: Path) -> dict[str, Any]:
    registry = AgentRegistry(project_dir)
    agents = []
    for agent in registry.list_agents():
        agents.append(
            {
                "name": agent.name,
                "provider": agent.provider,
                "status": agent.status.value,
                "priority": agent.priority,
                "supports_headless": agent.supports_headless,
                "last_used": agent.last_used,
                "last_error": agent.last_error,
                "last_credit_error": agent.last_credit_error,
                "health_reason": agent.health_reason,
                "cooldown_until": agent.cooldown_until,
                "reset_at": agent.reset_at,
                "success_count": agent.success_count,
                "failure_count": agent.failure_count,
                # Usage tracking fields
                "total_api_calls": agent.total_api_calls,
                "total_tokens_used": agent.total_tokens_used,
                "total_input_tokens": agent.total_input_tokens,
                "total_output_tokens": agent.total_output_tokens,
                "estimated_cost_usd": agent.estimated_cost_usd,
                "credits_remaining": agent.credits_remaining,
                "credits_limit": agent.credits_limit,
                "daily_usage": agent.daily_usage,
            }
        )

    return {
        "active_agent": registry.active_agent,
        "agents": agents,
        "history": registry.get_history(limit=8),
        "counts": {
            "total": len(agents),
            "available": sum(1 for item in agents if item["status"] == "available"),
            "blocked": sum(1 for item in agents if item["status"] in {"no_credits", "cooldown", "error"}),
        },
    }


def _chat_options_for(cwd: Path) -> list[dict[str, Any]]:
    options: list[str] = []
    resolved = cwd.resolve()

    if _has_project_state(resolved):
        spec = MemoryManager(resolved).spec()
        if spec.get("executor"):
            options.append(spec["executor"])
        registry = AgentRegistry(resolved)
        if registry.active_agent:
            options.append(registry.active_agent)
        options.extend(agent.name for agent in registry.list_agents())
    else:
        found = find_project_dir(resolved)
        if found and _has_project_state(found):
            spec = MemoryManager(found).spec()
            if spec.get("executor"):
                options.append(spec["executor"])
            registry = AgentRegistry(found)
            if registry.active_agent:
                options.append(registry.active_agent)
            options.extend(agent.name for agent in registry.list_agents())

    options.extend(["claude-code", "codex", "kiro"])
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in options:
        if name in seen or name not in CHAT_EXECUTOR_CONFIG:
            continue
        seen.add(name)
        cfg = CHAT_EXECUTOR_CONFIG[name]
        binary = cfg["cmd"][0]
        deduped.append(
            {
                "name": name,
                "label": cfg["label"],
                "available": bool(shutil.which(binary)),
                "resume": bool(cfg["resume"]),
            }
        )
    return deduped


def _resolve_chat_executor(cwd: Path, requested: str | None = None) -> dict[str, Any]:
    options = _chat_options_for(cwd)
    option_map = {opt["name"]: opt for opt in options}
    requested_name = (requested or "auto").strip()

    if requested_name and requested_name != "auto" and requested_name in option_map:
        selected = option_map[requested_name]
    else:
        selected = next((opt for opt in options if opt["available"]), None)
        if not selected and options:
            selected = options[0]

    if not selected:
        return {
            "requested_executor": requested_name or "auto",
            "resolved_executor": None,
            "executor": None,
            "label": "Agent",
            "available": False,
            "resume": False,
            "options": options,
            "placeholder": "No chat-capable agent configured",
            "greeting": "No chat-capable headless agent is configured for this project.",
        }

    label = selected["label"]
    return {
        "requested_executor": requested_name or "auto",
        "resolved_executor": selected["name"],
        "executor": selected["name"],
        "label": label,
        "available": bool(selected["available"]),
        "resume": bool(selected["resume"]),
        "options": options,
        "placeholder": f"Message {label}…",
        "greeting": f"Hey! I'm {label}. Ask me anything about your projects.",
    }


def _latest_runtime_records(project_dir: Path):
    runtime = get_runtime()
    slug = project_slug(project_dir)
    jobs = runtime.store.recent_jobs(slug, limit=1)
    runs = runtime.store.recent_agent_runs(slug, limit=1)
    return (jobs[0] if jobs else None), (runs[0] if runs else None)


def _runtime_pipeline_status(project_dir: Path, wf: WorkflowState) -> dict[str, Any]:
    job, run = _latest_runtime_records(project_dir)
    held = wf._process.get("held", False)
    is_running = bool(job and job.status in {"queued", "running"})

    if held:
        status = "held"
    elif wf.is_done():
        status = "done"
    elif wf.is_approval_gate():
        status = "waiting"
    elif is_running:
        status = "running"
    elif job and job.status == "failed":
        status = "stale"
    else:
        status = "stopped"

    return {
        "status": status,
        "pid": run.pid if is_running and run else None,
        "last_tick": None,
        "is_running": is_running,
        "at_gate": wf.is_approval_gate(),
        "held": held,
        "job_id": job.id if job else None,
        "job_status": job.status if job else None,
        "job_type": job.job_type if job else None,
        "run_id": run.id if run else None,
        "agent_name": run.agent_name if run else None,
        "skill": run.skill if run else None,
        "error": job.error if job else None,
    }


def _dispatch_current_phase(project_dir: Path, *, trigger: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    wf = WorkflowState(project_dir)
    status_payload = _runtime_pipeline_status(project_dir, wf)
    if status_payload["is_running"]:
        return {"ok": True, "already_running": True, **status_payload}
    if wf._process.get("held", False):
        return {
            "ok": False,
            "warning": "Project is held",
            "phase": wf.phase.value,
            "status": wf.status.value,
            **status_payload,
        }
    if wf.is_done():
        return {"ok": False, "warning": "Workflow is complete", "phase": wf.phase.value, "status": wf.status.value}
    if wf.is_approval_gate():
        return {
            "ok": False,
            "warning": "Workflow is awaiting approval",
            "phase": wf.phase.value,
            "status": wf.status.value,
            **status_payload,
        }

    sync_project_from_disk(project_dir, workflow_data=wf._data)
    spec = MemoryManager(project_dir).spec()
    run = get_runtime().spawn_for_project(
        project_dir,
        job_type=wf.phase.value,
        preferred_agent=spec.get("executor") or DEFAULT_EXECUTOR,
        trigger=trigger,
        allow_fallback=bool(spec.get("agent_fallback", True)),
        payload={"phase": wf.phase.value, "status": wf.status.value},
        metadata=metadata,
    )
    if run is None:
        return {
            "ok": False,
            "warning": "No runnable headless agent found",
            "phase": wf.phase.value,
            "status": wf.status.value,
        }

    job = get_runtime().store.get_job(run.job_id) if run.job_id else None
    return {
        "ok": True,
        "phase": wf.phase.value,
        "status": wf.status.value,
        "job_id": run.job_id,
        "run_id": run.id,
        "agent_name": run.agent_name,
        "skill": run.skill,
        "job_status": job.status if job else None,
    }


def _start_pipeline(project_dir: Path) -> dict[str, Any]:
    return _dispatch_current_phase(project_dir, trigger="api_start_pipeline")


def _bootstrap_project(body: dict[str, Any]) -> tuple[Path, str]:
    project_name = (body.get("project_name") or "").strip()
    if not project_name:
        raise HTTPException(400, "project_name is required")

    slug = _slugify(body.get("slug") or project_name)
    if not slug:
        raise HTTPException(400, "project_name must contain at least one letter or number")

    repo_root = _ui_repo_root()
    worktree_path, branch = _ensure_worktree(repo_root, slug)
    if (worktree_path / ".sdlc" / "spec.yaml").exists():
        raise HTTPException(409, f"Project '{slug}' already exists")

    init_sdlc_dirs(worktree_path)
    set_initial_state(worktree_path, "requirement")

    approvals_enabled = bool(body.get("require_approvals", True))
    executor = body.get("executor") or DEFAULT_EXECUTOR
    tech_stack = (body.get("tech_stack") or "").strip()
    description = (body.get("description") or "").strip()
    source_type = str(body.get("source_type") or ("manual" if body.get("source_text") or body.get("idea") else "unknown")).strip()
    source_label = str(body.get("source_label") or project_name).strip()
    source_location = str(body.get("source_path") or "").strip()
    source_content = _source_content_from_body(body)
    repo_binding = _repo_binding_from_body(body, repo_root)
    repo_value = repo_binding["repo_name"] or detect_remote_repo(repo_root)

    spec = {
        "project_name": project_name,
        "description": description,
        "tech_stack": tech_stack,
        "repo": repo_value,
        "slack_webhook": "",
        "executor": executor,
        "agent_fallback": bool(body.get("agent_fallback", True)),
        "phase_approvals": _approval_map(approvals_enabled),
    }

    mem = MemoryManager(worktree_path)
    mem.write_spec(spec)
    if not mem.project_path.exists():
        mem.write_project_memory(_project_template(project_name, description, tech_stack))
    else:
        mem.regenerate_claude_md()

    WorkflowState(worktree_path).set_branches(base_branch=branch, current_branch=branch)

    update_gitignore(worktree_path)

    registry = AgentRegistry(worktree_path)
    fallback_order = _known_agent_order(
        executor,
        [name.strip() for name in str(body.get("agent_order", "")).split(",") if name.strip()],
    )
    for idx, name in enumerate(fallback_order, start=1):
        if registry.get_agent(name):
            continue
        registry.add_agent(name, priority=idx, supports_headless=bool(EXECUTOR_CLI.get(name)))
    registry.reprioritize(fallback_order)
    registry.record_event(
        "project_initialized",
        f"Project {slug} initialized from dashboard",
        metadata={"executor": executor, "branch": branch},
    )
    runtime = get_runtime()
    runtime.store.upsert_project_source(
        project_slug=slug,
        source_type=source_type,
        label=source_label,
        location=source_location,
        content_text=source_content,
        metadata={
            "project_name": project_name,
            "description": description,
            "tech_stack": tech_stack,
        },
    )
    runtime.store.upsert_project_repo_binding(
        project_slug=slug,
        provider=repo_binding["provider"],
        mode=repo_binding["mode"],
        repo_name=repo_binding["repo_name"],
        repo_url=repo_binding["repo_url"],
        local_path=str(repo_root),
        is_new=bool(repo_binding["is_new"]),
        metadata=repo_binding["metadata"],
    )
    return worktree_path, slug


_ansi = re.compile(r'\x1b\[[0-9;]*[mGKHFJABCDsu]|\x1b\][^\x07]*\x07|\x1b\[?\?[0-9;]*[hl]|\r')
_SKIP = ('▸ Credits', 'Credits:', 'All tools are now trusted', 'Learn more at', 'Agents can sometimes',
         '✓ Successfully', '⋮', 'Summary:', 'Completed in', '- Completed')


@app.get("/api/chat/jobs")
def chat_jobs():
    running = [jid for jid, j in _jobs.items() if not j["done"]]
    return {"running": len(running), "job_ids": running}



@app.get("/api/chat")
async def chat(message: str, executor: str | None = None):
    """Start chat in background, return job_id immediately, client polls /api/chat/{job_id}."""
    import uuid
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "lines": [], "done": False}

    def _run():
        cwd = _chat_dir
        ctx = _build_sdlc_context()
        full_message = f"{ctx}\n\n{message}" if ctx else message
        meta = _resolve_chat_executor(cwd, executor)
        chosen = meta.get("executor")
        if not chosen:
            _jobs[job_id]["lines"].append(meta["greeting"])
            _append_job(job_id, meta["greeting"])
            _jobs[job_id]["done"] = True
            _append_job(job_id, done=True)
            return

        cfg = CHAT_EXECUTOR_CONFIG[chosen]
        binary = shutil.which(cfg["cmd"][0]) or cfg["cmd"][0]
        cwd_key = f"{cwd.resolve()}::{chosen}"
        has_session = False
        if cfg["resume"]:
            with _chat_lock:
                has_session = _chat_started.get(cwd_key, False)
                _chat_started[cwd_key] = True
        cmd = [binary, *cfg["cmd"][1:]]
        if chosen == "kiro":
            if has_session:
                cmd.append("--resume")
            cmd.append(full_message)
        else:
            cmd.append(full_message)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd)
            response_started = chosen != "kiro"
            for line in proc.stdout:
                line = _ansi.sub('', line).replace('\r', '')
                if chosen == "kiro" and not response_started:
                    if re.match(r'^>\s*\S', line):
                        response_started = True
                        line = re.sub(r'^>\s*', '', line)
                    else:
                        line = line.strip()
                        if line and not any(s in line for s in _SKIP):
                            _jobs[job_id]["lines"].append(f"⚙ {line}")
                            _append_job(job_id, f"⚙ {line}")
                        continue
                line = line.rstrip()
                if any(s in line for s in _SKIP):
                    continue
                if not line.strip():
                    continue
                _jobs[job_id]["lines"].append(line)
                _append_job(job_id, line)
            proc.wait()
        except Exception as e:
            _jobs[job_id]["lines"].append(f"ERROR: {e}")
            _append_job(job_id, f"ERROR: {e}")
        _jobs[job_id]["done"] = True
        _append_job(job_id, done=True)

    threading.Thread(target=_run, daemon=True).start()
    meta = _resolve_chat_executor(_chat_dir, executor)
    return {
        "job_id": job_id,
        "executor": meta.get("executor"),
        "resolved_executor": meta.get("resolved_executor"),
        "requested_executor": meta.get("requested_executor"),
        "label": meta.get("label"),
    }


@app.get("/api/chat/{job_id}")
def chat_poll(job_id: str, offset: int = 0):
    """Poll for new lines from a background chat job."""
    job = _jobs.get(job_id) or _load_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    lines = job["lines"][offset:]
    return {"lines": lines, "done": job["done"], "offset": offset + len(lines)}


# ── Projects ──────────────────────────────────────────────────────────────────


PHASES = [p.value for p in Phase if p != Phase.DONE]
PHASE_ARTIFACT_KEYS: dict[str, list[str]] = {
    Phase.REQUIREMENT.value: ["requirements", "requirement_questions", "test_spec"],
    Phase.DESIGN.value: ["design"],
    Phase.PLANNING.value: ["plan"],
    Phase.TESTING.value: ["test_cases", "test_results"],
    Phase.DOCUMENTATION.value: ["documentation", "docs", "review_summary"],
}
TESTING_ARTIFACT_KEYS = ["test_cases", "test_results"]
ARTIFACT_LABELS: dict[str, str] = {
    "requirement_questions": "Questions",
    "requirements": "Requirements",
    "test_spec": "Test spec",
    "design": "Design",
    "plan": "Plan",
    "test_cases": "Test cases",
    "test_results": "Test results",
    "documentation": "Documentation",
    "docs": "Docs",
    "review_summary": "Review summary",
}
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


def _repo_slug(repo_raw: str) -> str:
    repo = (repo_raw or "").strip()
    repo = repo.removeprefix("https://github.com/")
    repo = repo.removeprefix("http://github.com/")
    repo = repo.removeprefix("git@github.com:")
    repo = repo.removeprefix("github.com/")
    repo = repo.removesuffix(".git")
    return repo.strip("/")


def _flatten_values(value: Any) -> list[Any]:
    if value is None or value is False:
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[Any] = []
        for item in value:
            out.extend(_flatten_values(item))
        return out
    return [value]


def _first_url(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("url", "html_url", "permalink", "href"):
            url = value.get(key)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                return url
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    return ""


def _pr_url(repo: str, value: Any) -> str:
    if value is None or value is False:
        return ""
    if isinstance(value, dict):
        url = _first_url(value)
        if url:
            return url
        for key in ("number", "pr", "github_pr", "pull_request"):
            url = _pr_url(repo, value.get(key))
            if url:
                return url
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    url = _first_url(raw)
    if url:
        return url
    match = re.search(r"(?:#|pull/)?(\d+)$", raw)
    if repo and match:
        return f"https://github.com/{repo}/pull/{match.group(1)}"
    return ""


def _commit_urls(repo: str, *values: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    for value in values:
        for item in _flatten_values(value):
            if isinstance(item, dict):
                url = _first_url(item)
                if url:
                    add(url)
                    continue
                for key in ("sha", "hash", "commit", "commit_sha", "github_commit"):
                    sha = item.get(key)
                    if isinstance(sha, str) and _COMMIT_RE.match(sha.strip()) and repo:
                        add(f"https://github.com/{repo}/commit/{sha.strip()}")
                continue
            raw = str(item).strip()
            if raw.startswith(("http://", "https://")):
                add(raw)
            elif _COMMIT_RE.match(raw) and repo:
                add(f"https://github.com/{repo}/commit/{raw}")
    return urls


def _artifact_link(name: str, key: str) -> str:
    return f"/api/projects/{quote(name, safe='')}/artifact/{quote(key, safe='')}"


def _artifact_group(key: str) -> str:
    if key in TESTING_ARTIFACT_KEYS:
        return "testing"
    return _artifact_phase(key)


def _artifact_phase(key: str) -> str:
    for phase_key, keys in PHASE_ARTIFACT_KEYS.items():
        if key in keys:
            return phase_key
    return "other"


def _artifact_label(key: str) -> str:
    return ARTIFACT_LABELS.get(key, key.replace("_", " ").title())


def _state_link(name: str) -> str:
    return f"/api/projects/{quote(name, safe='')}/state"


def _artifact_path(project_dir: Path, raw_path: str) -> Path | None:
    raw = Path(str(raw_path)).expanduser()
    candidates = [raw] if raw.is_absolute() else [
        project_dir / raw,
        sdlc_home(project_dir) / raw,
        sdlc_home(project_dir) / "workflow" / "artifacts" / raw,
    ]
    if not raw.is_absolute():
        candidates.extend(root / raw for root in _global_repo_roots())
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved.exists():
            return resolved
    return None


def _safe_state_file(project_dir: Path, path: Path) -> Path:
    resolved = path.resolve()
    roots = [project_dir.resolve(), sdlc_home(project_dir).resolve()]
    roots.extend(root.resolve() for root in _global_repo_roots())
    if any(resolved == root or root in resolved.parents for root in roots):
        return resolved
    raise HTTPException(400, "Path is outside the SDLC project")


def _task_out(tid: str, raw_task: Any, repo: str) -> dict[str, Any]:
    task = raw_task if isinstance(raw_task, dict) else {"status": raw_task}
    out = {"id": tid, **task}
    out["commit_urls"] = _commit_urls(
        repo,
        task.get("commit"),
        task.get("commits"),
        task.get("commit_sha"),
        task.get("commit_shas"),
        task.get("github_commit"),
    )
    return out


def _project_data(project_dir: Path, name: str) -> dict[str, Any]:
    import os
    import time
    wf_dir = project_dir
    if not _has_project_state(wf_dir):
        return {"name": name, "error": "state unreadable"}
    try:
        wf = WorkflowState(wf_dir)
    except Exception:
        return {"name": name, "error": "state unreadable"}
    spec = MemoryManager(wf_dir).spec()

    repo = _repo_slug(spec.get("repo", ""))
    branch = wf._data.get("current_branch") or ""
    base_branch = wf._data.get("base_branch", "main")
    at_gate = wf.is_approval_gate()
    bypassed = [p for p, v in spec.get("phase_approvals", {}).items() if not v]

    # Build artifact links from state.json. These open the local artifact through
    # the dashboard so archived or not-yet-merged artifacts still work.
    artifact_links: dict[str, str] = {}
    artifact_items: list[dict[str, str]] = []
    phase_artifacts: dict[str, list[dict[str, str]]] = {phase: [] for phase in PHASES}
    for art_key, path in wf.artifacts.items():
        if not path:
            continue
        url = _artifact_link(name, art_key)
        artifact_links[art_key] = url
        item = {
            "key": art_key,
            "label": _artifact_label(art_key),
            "group": _artifact_group(art_key),
            "phase": _artifact_phase(art_key),
            "url": url,
        }
        artifact_items.append(item)
        if item["phase"] in phase_artifacts:
            phase_artifacts[item["phase"]].append(item)
    for phase_key, keys in PHASE_ARTIFACT_KEYS.items():
        for art_key in keys:
            if wf.artifacts.get(art_key):
                artifact_links[phase_key] = _artifact_link(name, art_key)
                break

    pr_links: dict[str, str] = {}
    commit_links: dict[str, list[str]] = {}
    for key, val in (wf._data.get("github_story_items", {}) or {}).items():
        url = _pr_url(repo, val)
        if url:
            pr_links[key] = url
    for phase_key, phase_val in wf._data.get("phases", {}).items():
        phase_pr = _pr_url(repo, phase_val.get("github_pr") or phase_val.get("pr"))
        phase_commits = _commit_urls(
            repo,
            phase_val.get("commit"),
            phase_val.get("commits"),
            phase_val.get("commit_sha"),
            phase_val.get("commit_shas"),
        )
        if phase_pr:
            pr_links[phase_key] = phase_pr
        if phase_commits:
            commit_links[phase_key] = phase_commits
        for sid, story in phase_val.get("stories", {}).items():
            pr = _pr_url(repo, story.get("github_pr") or story.get("pr") or story.get("pull_request"))
            commits = _commit_urls(
                repo,
                story.get("commit"),
                story.get("commits"),
                story.get("commit_sha"),
                story.get("commit_shas"),
                story.get("github_commit"),
            )
            if pr:
                pr_links[sid] = pr
                if sid == phase_key:
                    pr_links[phase_key] = pr
            if commits:
                commit_links[sid] = commits
                if sid == phase_key:
                    commit_links[phase_key] = commits

    # Build phase list directly from phases dict in state.json.
    phase_data = wf._data.get("phases", {})
    phases_out = []
    for p in PHASES:
        pd = phase_data.get(p, {})
        ph_status = pd.get("status", Status.PENDING.value)
        stories_state = pd.get("stories", {})
        stories = []
        for sid, s in sorted(stories_state.items()):
            story = {
                "id": sid,
                "name": s.get("name"),
                "status": s.get("status", "pending"),
                "github_issue": s.get("github_issue"),
                "github_pr": s.get("github_pr"),
                "pr_url": pr_links.get(sid),
                "commit_urls": commit_links.get(sid, []),
                "current_task": s.get("current_task"),
                "tasks": [
                    _task_out(tid, tst, repo)
                    for tid, tst in s.get("tasks", {}).items()
                ],
            }
            stories.append(story)
        entry: dict[str, Any] = {
            "name": p,
            "status": ph_status,
            "pr_url": pr_links.get(p),
            "artifact_url": artifact_links.get(p),
            "artifact_items": phase_artifacts.get(p, []),
            "commit_urls": commit_links.get(p, []),
            "stories": stories,
        }
        phases_out.append(entry)

    impl_phase = next((ph for ph in phases_out if ph["name"] == Phase.IMPLEMENTATION.value), {})
    registry_data = _agent_registry_data(project_dir)
    intake = _project_intake_data(name)
    return {
        "name": name,
        "display_name": spec.get("project_name", name),
        "phase": wf.phase.value,
        "status": wf.status.value,
        "label": wf.label(),
        "phase_total": len(PHASES),
        "phases": phases_out,
        "stories": impl_phase.get("stories", []),
        "at_gate": at_gate,
        "bypassed_phases": bypassed,
        "current_story": wf.current_story,
        "story_status": wf.story_status.value if wf.story_status else None,
        "current_task": wf.current_task,
        "completed_stories": wf.completed_stories,
        "last_updated": wf._data.get("last_updated", "")[:19],
        "repo": repo,
        "branch": branch,
        "base_branch": base_branch,
        "state_url": _state_link(name),
        "pr_links": pr_links,
        "commit_links": commit_links,
        "artifact_links": artifact_links,
        "artifact_items": artifact_items,
        "held": wf._process.get("held", False),
        "pipeline_status": _runtime_pipeline_status(project_dir, wf),
        "agent_registry": registry_data,
        "source": intake["source"],
        "repo_binding": intake["repo_binding"],
    }


@app.get("/api/projects")
def get_projects():
    active = []
    closed = []
    seen_keys: set[Path] = set()

    for project_dir, name, archived in _db_project_entries():
        key = project_dir.resolve()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if not name:
            continue
        data = _project_data(project_dir, name)
        if data.get("error"):
            continue
        if archived:
            data["archived"] = True
            closed.append(data)
        elif data.get("phase") == Phase.DONE.value or data.get("closed"):
            closed.append(data)
        else:
            active.append(data)

    return {"active": active, "closed": closed}


@app.get("/api/intake/github/repos")
def github_repos(limit: int = 100):
    if not github.is_available():
        return {"available": False, "repos": []}
    return {"available": True, "repos": github.list_repositories(limit=limit)}


@app.post("/api/intake/github/repos")
def create_github_repo(body: dict[str, Any]):
    if not github.is_available():
        raise HTTPException(400, "GitHub CLI is not available")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    created = github.create_repository(
        name,
        description=str(body.get("description") or "").strip(),
        private=bool(body.get("private", True)),
    )
    if not created:
        raise HTTPException(400, f"Failed to create repository '{name}'")
    return {"ok": True, "repo": created}


@app.post("/api/projects/start")
def create_project(body: dict[str, Any]):
    project_dir, slug = _bootstrap_project(body)
    start_pipeline = bool(body.get("start_pipeline", body.get("start_loop", False)))
    pipeline_result = _dispatch_current_phase(project_dir, trigger="project_start") if start_pipeline else None
    return {
        "ok": True,
        "project": _project_data(project_dir, slug),
        "pipeline": pipeline_result,
    }


@app.get("/api/projects/{name}/intake")
def get_project_intake(name: str):
    _resolve_project_dir(name)
    return _project_intake_data(name)


@app.get("/api/projects/{name}/state", response_class=PlainTextResponse)
def get_state_json(name: str):
    wf_dir = _resolve_project_dir(name)
    wf = WorkflowState(wf_dir)
    return PlainTextResponse(wf.path.read_text(), media_type="application/json")


@app.get("/api/projects/{name}/agents")
def get_agents(name: str):
    project_dir = _resolve_project_dir(name)
    return _agent_registry_data(project_dir)


@app.post("/api/projects/{name}/agents/reset")
def reset_all_agents(name: str):
    project_dir = _resolve_project_dir(name)
    registry = AgentRegistry(project_dir)
    registry.reset_all()
    return {"ok": True, "registry": _agent_registry_data(project_dir)}


@app.post("/api/projects/{name}/agents/{agent_name}/reset")
def reset_agent(name: str, agent_name: str):
    project_dir = _resolve_project_dir(name)
    registry = AgentRegistry(project_dir)
    if not registry.get_agent(agent_name):
        raise HTTPException(404, f"Agent not found: {agent_name}")
    registry.reset_agent(agent_name)
    return {"ok": True, "registry": _agent_registry_data(project_dir)}


@app.get("/api/projects/{name}/artifact/{key}")
def get_artifact(name: str, key: str, raw: bool = False):
    wf_dir = _resolve_project_dir(name)
    wf = WorkflowState(wf_dir)
    raw_path = wf.artifacts.get(key)
    if not raw_path:
        raise HTTPException(404, f"No artifact recorded for '{key}'")
    if isinstance(raw_path, str) and raw_path.startswith(("http://", "https://")):
        return RedirectResponse(raw_path)
    artifact = _artifact_path(wf_dir, raw_path)

    # Read content
    if artifact and artifact.exists() and artifact.is_file():
        safe_path = _safe_state_file(wf_dir, artifact)
        content = safe_path.read_text(errors="replace")
        artifact_found = True
    else:
        # Artifact file not found - create helpful error message
        content = f"""# Artifact Not Found

The artifact **{_artifact_label(key)}** is referenced but the file could not be found.

**Expected path:** `{raw_path}`

**Searched locations:**
- `{wf_dir / raw_path}` (relative to project)
- `{sdlc_home(wf_dir) / raw_path}` (relative to .sdlc)
- `{sdlc_home(wf_dir) / "workflow" / "artifacts" / raw_path}` (in artifacts folder)

The file may not have been created yet, or the path may be incorrect in the workflow state.
"""
        artifact_found = False

    # Return raw content if requested
    if raw:
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    # Render with beautiful HTML viewer
    template_path = Path(__file__).parent / "artifact_viewer.html"
    if not template_path.exists():
        # Fallback to plain text if template not found
        return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")

    html_template = template_path.read_text()

    # Determine artifact type label
    artifact_type = _artifact_label(key)
    if not artifact_found:
        artifact_type = f"{artifact_type} (Not Found)"

    # Replace placeholders
    html = html_template.replace("{{TITLE}}", f"{artifact_type}")
    html = html.replace("{{TYPE}}", key.upper())

    # Escape content for safe HTML embedding
    import html as html_module
    escaped_content = html_module.escape(content)
    html = html.replace("{{CONTENT}}", escaped_content)

    return HTMLResponse(html)


@app.post("/api/projects/{name}/approve")
def approve(name: str):
    wf_dir = _resolve_project_dir(name)
    service = get_workflow_service()
    wf = service.load(wf_dir)
    if not wf.is_approval_gate():
        raise HTTPException(400, f"Not an approval gate: {wf.phase.value}:{wf.status.value}")

    approved_phase = wf.phase.value
    approved_status = wf.status.value
    wf = service.approve(wf_dir)
    sync_project_from_disk(wf_dir, workflow_data=wf._data)
    approval = record_approval_event(
        wf_dir,
        phase=approved_phase,
        source="ui_api",
        state="approved",
        payload={
            "from_phase": approved_phase,
            "from_status": approved_status,
            "to_phase": wf.phase.value,
            "to_status": wf.status.value,
        },
    )
    return {
        "ok": True,
        "workflow": {"phase": wf.phase.value, "status": wf.status.value, "label": wf.label()},
        "approval": {"id": approval.id, "phase": approval.phase, "state": approval.state},
    }


@app.post("/api/projects/{name}/no-approvals")
def no_approvals(name: str):
    wf_dir = _resolve_project_dir(name)
    result = subprocess.run(["sdlc", "state", "no-approvals"], cwd=wf_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or result.stdout)
    return {"ok": True}


@app.post("/api/projects/{name}/hold")
def hold_project(name: str):
    wf_dir = _resolve_project_dir(name)
    WorkflowState(wf_dir).set_process(held=True)
    return {"ok": True}


@app.post("/api/projects/{name}/resume")
def resume_project(name: str):
    wf_dir = _resolve_project_dir(name)
    WorkflowState(wf_dir).set_process(held=False)
    dispatch = _dispatch_current_phase(wf_dir, trigger="ui_resume")
    return {"ok": True, "dispatch": dispatch}


@app.post("/api/projects/{name}/approvals")
def restore_approvals(name: str):
    wf_dir = _resolve_project_dir(name)
    result = subprocess.run(["sdlc", "state", "approvals"], cwd=wf_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or result.stdout)
    return {"ok": True}


@app.get("/api/projects/{name}/prs")
def get_prs(name: str):
    """Return open/merged PRs for all SDLC branches of this project."""
    try:
        roots = _global_repo_roots()
        gh_cwd = roots[0] if roots else _project_dir
        result = subprocess.run(
            ["gh", "pr", "list", "--search", f"sdlc-{name}", "--state", "all",
             "--json", "number,title,url,state,headRefName,mergedAt,createdAt"],
            capture_output=True, text=True, cwd=gh_cwd
        )
        if result.returncode != 0:
            return {"prs": []}
        import json as _json
        prs = _json.loads(result.stdout or "[]")
        return {"prs": prs}
    except Exception:
        return {"prs": []}


@app.get("/api/projects/{name}/history")
def get_history(name: str):
    wf_dir = _resolve_project_dir(name)
    try:
        wf = WorkflowState(wf_dir)
        return {"history": wf._data.get("history", [])}
    except Exception:
        return {"history": []}


@app.get("/api/projects/{name}/evidence")
def get_evidence_index(name: str):
    """List all evidence files for a project."""
    import json as _json
    from sdlc_orchestrator.utils import project_slug as _slug

    wf_dir = _resolve_project_dir(name)
    slug = _slug(wf_dir)

    # Try to find evidence directory in multiple locations
    evidence_candidates = [
        wf_dir / "docs" / "sdlc" / slug / "evidence",
        sdlc_home(wf_dir) / "docs" / "sdlc" / slug / "evidence",
    ]
    for root in _global_repo_roots():
        evidence_candidates.append(root / "docs" / "sdlc" / slug / "evidence")

    evidence_dir = None
    for candidate in evidence_candidates:
        if candidate.exists() and candidate.is_dir():
            evidence_dir = candidate
            break

    if not evidence_dir:
        return {"evidence": [], "evidence_dir": None}

    # Try to load index.json if it exists
    index_file = evidence_dir / "index.json"
    if index_file.exists():
        try:
            evidence_index = _json.loads(index_file.read_text())
            return {"evidence": evidence_index, "evidence_dir": str(evidence_dir)}
        except Exception:
            pass

    # Fallback: scan directory and build index
    evidence_files = []
    for evidence_file in sorted(evidence_dir.glob("*")):
        if evidence_file.name == "index.json":
            continue
        if not evidence_file.is_file():
            continue

        file_type = "unknown"
        if evidence_file.suffix == ".json":
            file_type = "api_response" if "response" in evidence_file.name else "metrics"
        elif evidence_file.suffix in (".png", ".jpg", ".jpeg", ".gif"):
            file_type = "screenshot"
        elif evidence_file.suffix == ".txt":
            file_type = "logs"
        elif evidence_file.suffix == ".html":
            file_type = "report"

        # Extract test_id from filename (TC-XXX)
        test_id = None
        if evidence_file.name.startswith("TC-"):
            parts = evidence_file.name.split("-")
            if len(parts) >= 2:
                test_id = f"{parts[0]}-{parts[1]}"

        evidence_files.append({
            "file": evidence_file.name,
            "test_id": test_id,
            "type": file_type,
            "size": evidence_file.stat().st_size,
        })

    return {"evidence": evidence_files, "evidence_dir": str(evidence_dir)}


@app.get("/api/projects/{name}/evidence/{filename}")
def get_evidence_file(name: str, filename: str):
    """Retrieve a specific evidence file."""
    from sdlc_orchestrator.utils import project_slug as _slug
    import json as _json

    wf_dir = _resolve_project_dir(name)
    slug = _slug(wf_dir)

    # Find evidence directory
    evidence_candidates = [
        wf_dir / "docs" / "sdlc" / slug / "evidence",
        sdlc_home(wf_dir) / "docs" / "sdlc" / slug / "evidence",
    ]
    for root in _global_repo_roots():
        evidence_candidates.append(root / "docs" / "sdlc" / slug / "evidence")

    evidence_file = None
    for candidate_dir in evidence_candidates:
        candidate_file = candidate_dir / filename
        if candidate_file.exists() and candidate_file.is_file():
            evidence_file = candidate_file
            break

    if not evidence_file:
        raise HTTPException(404, f"Evidence file not found: {filename}")

    # Security check: ensure file is within evidence directory
    try:
        resolved = evidence_file.resolve()
        if not any(c.resolve() in resolved.parents for c in evidence_candidates if c.exists()):
            raise HTTPException(403, "Access denied")
    except Exception:
        raise HTTPException(403, "Access denied")

    # Determine content type and return appropriate response
    suffix = evidence_file.suffix.lower()

    if suffix == ".json":
        content = evidence_file.read_text()
        return PlainTextResponse(content, media_type="application/json")

    elif suffix in (".png", ".jpg", ".jpeg", ".gif"):
        import mimetypes
        mime_type = mimetypes.guess_type(str(evidence_file))[0] or "image/png"
        return StreamingResponse(
            open(evidence_file, "rb"),
            media_type=mime_type,
            headers={"Content-Disposition": f'inline; filename="{filename}"'}
        )

    elif suffix == ".html":
        content = evidence_file.read_text()
        return HTMLResponse(content)

    else:  # .txt and others
        content = evidence_file.read_text(errors="replace")
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


@app.get("/api/projects/{name}/status")
def get_pipeline_status(name: str):
    """Return runtime-backed orchestrator status for the project."""
    wf_dir = _resolve_project_dir(name)
    try:
        wf = WorkflowState(wf_dir)
        return _runtime_pipeline_status(wf_dir, wf)
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/projects/{name}/start-pipeline")
def start_pipeline(name: str):
    """Kick off the current phase through the Python orchestrator runtime."""
    wf_dir = _resolve_project_dir(name)
    return _start_pipeline(wf_dir)


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the React dashboard."""
    return react_dashboard()


@app.get("/react", response_class=HTMLResponse)
def react_dashboard():
    """Serve the React dashboard."""
    react_build = Path(__file__).parent / "react-app" / "dist" / "index.html"
    if react_build.exists():
        return react_build.read_text()
    return HTMLResponse("""
        <!DOCTYPE html>
        <html>
        <head><title>SDLC Dashboard</title></head>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h1>Dashboard Not Built</h1>
            <p>Run <code>npm run build</code> in <code>sdlc_orchestrator/ui/react-app</code> to build the UI.</p>
        </body>
        </html>
    """, status_code=503)

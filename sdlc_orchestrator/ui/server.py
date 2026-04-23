"""Minimal FastAPI server for the SDLC dashboard."""
from __future__ import annotations

import asyncio
import inspect
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse

from sdlc_orchestrator import __version__
from sdlc_orchestrator.state_machine import Phase, Status, WorkflowState
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.utils import get_active_project, sdlc_home

app = FastAPI(title="SDLC Dashboard")

# Injected at startup
_project_dir: Path = Path(".")
_chat_dir: Path = Path(".")

_chat_started: dict[str, bool] = {}
_chat_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_jobs_dir: Path = Path.home() / ".sdlc" / "chat-jobs"
_jobs_dir.mkdir(parents=True, exist_ok=True)


def _runtime_meta() -> dict[str, str]:
    package_file = Path(inspect.getfile(__import__("sdlc_orchestrator"))).resolve()
    package_root = package_file.parent
    dashboard_file = (Path(__file__).parent / "dashboard.html").resolve()
    source_mode = "installed" if "site-packages" in package_root.parts else "repo"
    return {
        "version": __version__,
        "source_mode": source_mode,
        "package_root": str(package_root),
        "server_file": str(Path(__file__).resolve()),
        "dashboard_file": str(dashboard_file),
        "project_dir": str(_project_dir.resolve()),
    }


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
    import json
    with open(_job_path(job_id), "a") as f:
        if line is not None:
            f.write(json.dumps({"line": line}) + "\n")
        if done:
            f.write(json.dumps({"done": True}) + "\n")


# ── Filesystem browse ─────────────────────────────────────────────────────────

@app.get("/api/fs/browse")
def fs_browse(path: str = "~"):
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        p = p.parent
    dirs = sorted([d.name for d in p.iterdir() if d.is_dir() and not d.name.startswith('.')], key=str.lower)
    return {"path": str(p), "parent": str(p.parent), "dirs": dirs}


@app.get("/api/meta")
def runtime_meta():
    return _runtime_meta()


# ── Chat CWD ──────────────────────────────────────────────────────────────────

@app.get("/api/chat/cwd")
def get_cwd():
    return {"cwd": str(_chat_dir)}


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

def _build_sdlc_context() -> str:
    """Build a brief SDLC state summary to inject as context."""
    try:
        entries = _known_project_entries()
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


_ansi = re.compile(r'\x1b\[[0-9;]*[mGKHFJABCDsu]|\x1b\][^\x07]*\x07|\x1b\[?\?[0-9;]*[hl]|\r')
_SKIP = ('▸ Credits', 'Credits:', 'All tools are now trusted', 'Learn more at', 'Agents can sometimes',
         '✓ Successfully', '⋮', 'Summary:', 'Completed in', '- Completed')


@app.get("/api/chat/jobs")
def chat_jobs():
    running = [jid for jid, j in _jobs.items() if not j["done"]]
    return {"running": len(running), "job_ids": running}



@app.get("/api/chat")
async def chat(message: str):
    """Start chat in background, return job_id immediately, client polls /api/chat/{job_id}."""
    import uuid
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "lines": [], "done": False}

    def _run():
        cli = shutil.which("kiro-cli") or "kiro-cli"
        cwd = _chat_dir
        cwd_key = str(cwd)
        ctx = _build_sdlc_context()
        full_message = f"{ctx}\n\n{message}" if ctx else message
        with _chat_lock:
            has_session = _chat_started.get(cwd_key, False)
            _chat_started[cwd_key] = True
        cmd = [cli, "chat", "--no-interactive", "--trust-all-tools"]
        if has_session:
            cmd.append("--resume")
        cmd.append(full_message)
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd)
            response_started = False
            for line in proc.stdout:
                line = _ansi.sub('', line).replace('\r', '')
                if not response_started:
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
                _jobs[job_id]["lines"].append(line)
                _append_job(job_id, line)
            proc.wait()
        except Exception as e:
            _jobs[job_id]["lines"].append(f"ERROR: {e}")
            _append_job(job_id, f"ERROR: {e}")
        _jobs[job_id]["done"] = True
        _append_job(job_id, done=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id}


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

    proc = wf._process
    held = proc.get("held", False)
    pid = proc.get("pid")
    last_tick = proc.get("last_tick")
    is_running = False
    if pid:
        try:
            os.kill(pid, 0)
            is_running = True
        except (OSError, ProcessLookupError):
            pass
    if held:
        proc_status = "held"
    elif wf.is_done():
        proc_status = "done"
    elif is_running:
        if last_tick and (time.time() - last_tick) > 900:
            proc_status = "stale"
        elif at_gate:
            proc_status = "waiting"
        else:
            proc_status = "running"
    else:
        proc_status = "stopped"

    impl_phase = next((ph for ph in phases_out if ph["name"] == Phase.IMPLEMENTATION.value), {})
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
        "held": held,
        "process_status": {"status": proc_status, "pid": pid, "last_tick": last_tick, "is_running": is_running},
    }


@app.get("/api/projects")
def get_projects():
    # Collect active project entries from all known repo roots (global)
    entries = list(_known_project_entries())
    seen_dirs = {pd.resolve() for pd, _ in entries}

    active = []
    closed = []
    seen_keys: set[Path] = set()

    for project_dir, name in entries:
        key = project_dir.resolve()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if not name:
            continue
        data = _project_data(project_dir, name)
        if data.get("error"):
            continue
        if data.get("phase") == Phase.DONE.value or data.get("closed"):
            closed.append(data)
        else:
            active.append(data)

    # Add archived projects from .projects/ (always closed/done)
    for project_dir, name in _known_archive_entries():
        key = project_dir.resolve()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        data = _project_data(project_dir, name)
        if data.get("error"):
            continue
        data["archived"] = True
        closed.append(data)

    return {"active": active, "closed": closed}


@app.get("/api/projects/{name}/state", response_class=PlainTextResponse)
def get_state_json(name: str):
    wf_dir = _resolve_project_dir(name)
    wf = WorkflowState(wf_dir)
    return PlainTextResponse(wf.path.read_text(), media_type="application/json")


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
    result = subprocess.run(["sdlc", "state", "approve"], cwd=wf_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or result.stdout)
    return {"ok": True, "output": result.stdout.strip()}


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
    return {"ok": True}


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
def get_process_status(name: str):
    """Detect if loop process is running, stopped, or waiting for approval."""
    import os
    import time
    wf_dir = _resolve_project_dir(name)
    try:
        wf = WorkflowState(wf_dir)
        proc = wf._process
        pid = proc.get("pid")
        last_tick = proc.get("last_tick")
        held = proc.get("held", False)
        at_gate = wf.is_approval_gate()

        is_running = False
        if pid:
            try:
                os.kill(pid, 0)
                is_running = True
            except (OSError, ProcessLookupError):
                pass

        if held:
            status = "held"
        elif wf.is_done():
            status = "done"
        elif is_running:
            if last_tick and (time.time() - last_tick) > 600:
                status = "stale"
            elif at_gate:
                status = "waiting"
            else:
                status = "running"
        else:
            status = "stopped"

        return {
            "status": status,
            "pid": pid,
            "last_tick": last_tick,
            "is_running": is_running,
            "at_gate": at_gate,
            "held": held,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/projects/{name}/start-loop")
def start_loop(name: str):
    """Start the orchestration loop in background."""
    import subprocess
    from sdlc_orchestrator.memory import MemoryManager, EXECUTOR_CLI

    wf_dir = _resolve_project_dir(name)

    spec = MemoryManager(wf_dir).spec()
    executor = spec.get("executor", "kiro")
    cmd_template = EXECUTOR_CLI.get(executor, EXECUTOR_CLI["kiro"])
    if not cmd_template:
        raise HTTPException(400, f"Executor '{executor}' has no headless CLI")

    skill = "sdlc-orchestrate"
    loop_cmd = " ".join(
        part.replace("{skill}", skill) for part in cmd_template
    )
    bash_cmd = f"while true; do {loop_cmd}; sleep 600; done"

    try:
        import time as _time
        proc = subprocess.Popen(
            ["bash", "-c", bash_cmd],
            cwd=str(wf_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        WorkflowState(wf_dir).set_process(pid=proc.pid, last_tick=_time.time())
        return {"ok": True, "pid": proc.pid, "cwd": str(wf_dir), "executor": executor}
    except Exception as e:
        raise HTTPException(500, f"Failed to start loop: {e}")


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = Path(__file__).parent / "dashboard.html"
    return html_path.read_text()

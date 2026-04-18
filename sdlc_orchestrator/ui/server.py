"""Minimal FastAPI server for the SDLC dashboard."""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from sdlc_orchestrator.state_machine import APPROVAL_STATES, WorkflowState
from sdlc_orchestrator.memory import MemoryManager

app = FastAPI(title="SDLC Dashboard")

# Injected at startup
_project_dir: Path = Path(".")
_chat_dir: Path = Path(".")

_chat_started: dict[str, bool] = {}
_chat_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_jobs_dir: Path = Path.home() / ".sdlc" / "chat-jobs"
_jobs_dir.mkdir(parents=True, exist_ok=True)


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

def _build_sdlc_context() -> str:
    """Build a brief SDLC state summary to inject as context."""
    try:
        active_file = _project_dir / ".sdlc" / "active"
        if not active_file.exists():
            return ""
        names = [n.strip() for n in active_file.read_text().splitlines() if n.strip()]
        lines = ["Current SDLC project states (as of now):"]
        for name in names:
            worktree = _project_dir / "worktree" / name
            wf_dir = worktree if worktree.exists() else _project_dir
            try:
                wf = WorkflowState(wf_dir)
                spec = MemoryManager(wf_dir).spec()
                display = spec.get("project_name", name)
                state = wf.state.value
                done = len(wf.completed_stories)
                lines.append(f"- {display} (slug: {name}): state={state}, completed_stories={done}, last_updated={wf._data.get('last_updated','')[:19]}")
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

PHASES = ["requirement", "design", "planning", "implementation", "documentation"]

PHASE_STATES = {
    "requirement":    ("requirement_in_progress", "requirement_ready_for_approval"),
    "design":         ("design_in_progress", "awaiting_design_approval"),
    "planning":       ("task_plan_in_progress", "task_plan_ready"),
    "implementation": ("story_in_progress", "story_awaiting_review", "feedback_incorporation"),
    "documentation":  ("documentation_in_progress", "documentation_awaiting_approval"),
}

STATE_PHASE_INDEX: dict[str, int] = {}
for _i, (_phase, _states) in enumerate(PHASE_STATES.items()):
    for _s in _states:
        STATE_PHASE_INDEX[_s] = _i
STATE_PHASE_INDEX["done"] = len(PHASES)


def _parse_plan(plan_path: Path) -> dict[str, dict]:
    text = None
    if plan_path.exists():
        text = plan_path.read_text()
    else:
        # Try reading from git branch sdlc-<name>-plan
        import subprocess
        fname = plan_path.name
        slug = re.sub(r'^sdlc-|-plan\.md$', '', fname)
        branch = f"sdlc-{slug}-plan"
        repo = plan_path.parent.parent  # docs/../ = repo root
        try:
            result = subprocess.run(
                ["git", "show", f"{branch}:{plan_path.relative_to(repo)}"],
                cwd=repo, capture_output=True, text=True
            )
            if result.returncode == 0:
                text = result.stdout
        except Exception:
            pass
    if not text:
        return {}
    stories: dict[str, dict] = {}
    current = None
    current_task = None
    for line in text.splitlines():
        sm = re.match(r"^#{1,2}\s+(STORY-[\w]+)[:：]?\s*(.*)", line)
        if sm:
            current = sm.group(1)
            stories[current] = {"title": sm.group(2).strip(), "tasks": []}
            current_task = None
            continue
        if not current:
            continue
        # Task as heading: ## TASK-NNN: title
        tm = re.match(r"^#{1,3}\s+(TASK-[\w-]+)[:：]?\s*(.*)", line)
        if tm:
            current_task = {"id": tm.group(1), "title": tm.group(2).strip(), "done": False}
            stories[current]["tasks"].append(current_task)
            continue
        # Task as checkbox: - [ ] TASK-NNN: title
        cm = re.match(r"^\s*[-*]\s+\[([ x])\]\s*(TASK-\d+)[:：]?\s*(.*)", line, re.I)
        if cm:
            current_task = {"id": cm.group(2), "title": cm.group(3).strip(), "done": cm.group(1).lower() == "x"}
            stories[current]["tasks"].append(current_task)
            continue
        # Status line: - Status: [x]
        if current_task:
            st = re.match(r"^\s*[-*]?\s*Status:\s*\[([ x])\]", line, re.I)
            if st:
                current_task["done"] = st.group(1).lower() == "x"
    return stories


def _project_data(project_dir: Path, name: str) -> dict[str, Any]:
    worktree = project_dir / "worktree" / name
    wf_dir = worktree if worktree.exists() else project_dir
    try:
        wf = WorkflowState(wf_dir)
    except Exception:
        return {"name": name, "error": "state unreadable"}
    spec = MemoryManager(wf_dir).spec()
    state = wf.state.value
    phase_idx = STATE_PHASE_INDEX.get(state, 0)
    phase_status = []
    for i, phase in enumerate(PHASES):
        if i < phase_idx:
            status = "done"
        elif i == phase_idx:
            status = "active"
        else:
            status = "pending"
        phase_status.append({"name": phase, "status": status})
    plan_path = wf_dir / f"docs/sdlc-{name}-plan.md"
    if not plan_path.exists():
        # Try repo root (parent of worktree dir)
        plan_path = wf_dir.parent.parent / f"docs/sdlc-{name}-plan.md"
    if not plan_path.exists():
        plan_path = wf_dir.parent / f"docs/sdlc-{name}-plan.md"
    stories_data = _parse_plan(plan_path)
    stories = []
    for sid, sdata in stories_data.items():
        done = sid in wf.completed_stories
        active = sid == wf.current_story
        stories.append({"id": sid, "title": sdata["title"], "status": "done" if done else ("active" if active else "pending"), "tasks": sdata["tasks"]})
    at_gate = wf.state in APPROVAL_STATES
    bypassed = [p for p, v in spec.get("phase_approvals", {}).items() if not v]

    repo_raw = spec.get("repo", "")
    repo = repo_raw.replace("github.com/", "").replace("https://github.com/", "") if repo_raw else ""
    branch = wf._data.get("current_branch", "")
    story_items = wf._data.get("github_story_items", {})
    pr_links = {}
    if repo:
        for key, val in story_items.items():
            num = val.get("number")
            if num:
                pr_links[key] = f"https://github.com/{repo}/pull/{num}"

    return {
        "name": name,
        "display_name": spec.get("project_name", name),
        "state": state,
        "phase_index": phase_idx,
        "phase_total": len(PHASES),
        "phases": phase_status,
        "stories": stories,
        "at_gate": at_gate,
        "bypassed_phases": bypassed,
        "current_story": wf.current_story,
        "completed_stories": wf.completed_stories,
        "last_updated": wf._data.get("last_updated", "")[:19],
        "repo": repo,
        "branch": branch,
        "pr_links": pr_links,
    }


@app.get("/api/projects")
def get_projects():
    from sdlc_orchestrator.commands.ops import _find_all_project_dirs
    # Collect all known projects from ~/.sdlc/projects symlinks + local .sdlc/active
    entries = _find_all_project_dirs(scan_dir=_project_dir.parent)
    seen_dirs = {pd for pd, _ in entries}

    # Also include projects from local active file if not already found
    active_file = _project_dir / ".sdlc" / "active"
    if active_file.exists():
        for name in [n.strip() for n in active_file.read_text().splitlines() if n.strip()]:
            worktree = _project_dir / "worktree" / name
            wf_dir = worktree if worktree.exists() else _project_dir
            if wf_dir not in seen_dirs:
                entries.append((wf_dir, name))
                seen_dirs.add(wf_dir)

    active = []
    closed = []
    seen_names: set[str] = set()
    for project_dir, name in entries:
        if not name or name == "default":
            # Try to get name from spec.yaml
            try:
                spec = MemoryManager(project_dir).spec()
                pname = spec.get("project_name", "")
                name = pname.lower().replace(" ", "-") if pname else ""
            except Exception:
                pass
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        data = _project_data(project_dir, name)
        if data.get("error"):
            continue
        if data.get("closed"):
            closed.append(data)
        else:
            active.append(data)

    return {"active": active, "closed": closed}


@app.post("/api/projects/{name}/approve")
def approve(name: str):
    worktree = _project_dir / "worktree" / name
    wf_dir = worktree if worktree.exists() else _project_dir
    result = subprocess.run(["sdlc", "state", "approve"], cwd=wf_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or result.stdout)
    return {"ok": True, "output": result.stdout.strip()}


@app.post("/api/projects/{name}/no-approvals")
def no_approvals(name: str):
    worktree = _project_dir / "worktree" / name
    wf_dir = worktree if worktree.exists() else _project_dir
    result = subprocess.run(["sdlc", "state", "no-approvals"], cwd=wf_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or result.stdout)
    return {"ok": True}


@app.post("/api/projects/{name}/approvals")
def restore_approvals(name: str):
    worktree = _project_dir / "worktree" / name
    wf_dir = worktree if worktree.exists() else _project_dir
    result = subprocess.run(["sdlc", "state", "approvals"], cwd=wf_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(400, result.stderr or result.stdout)
    return {"ok": True}


@app.get("/api/projects/{name}/prs")
def get_prs(name: str):
    """Return open/merged PRs for all SDLC branches of this project."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--search", f"sdlc-{name}", "--state", "all",
             "--json", "number,title,url,state,headRefName,mergedAt,createdAt"],
            capture_output=True, text=True, cwd=_project_dir
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
    worktree = _project_dir / "worktree" / name
    wf_dir = worktree if worktree.exists() else _project_dir
    try:
        wf = WorkflowState(wf_dir)
        return {"history": wf._data.get("history", [])}
    except Exception:
        return {"history": []}


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = Path(__file__).parent / "dashboard.html"
    return html_path.read_text()

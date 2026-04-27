from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable

from sdlc_orchestrator.agent_registry import AgentRegistry, AgentStatus
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.utils import project_slug


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True)


def _tail(value: str | None, limit: int = 8000) -> str | None:
    if value is None:
        return None
    return value[-limit:] if len(value) > limit else value


DATABASE_PATH = Path.home() / ".sdlc" / "backend.sqlite3"

PHASE_SKILL_MAP: dict[str, str] = {
    "requirement": "sdlc-requirement",
    "requirements": "sdlc-requirement",
    "design": "sdlc-design",
    "planning": "sdlc-plan",
    "plan": "sdlc-plan",
    "implementation": "sdlc-implement",
    "implement_story": "sdlc-implement",
    "testing": "sdlc-validate",
    "validate": "sdlc-validate",
    "review": "sdlc-review",
    "feedback": "sdlc-feedback",
    "cleanup_worktree": "sdlc-cleanup-worktree",
}


def resolve_phase_skill(phase_name: str) -> str:
    normalized = (phase_name or "").strip().lower().replace("-", "_")
    return PHASE_SKILL_MAP.get(normalized) or normalized


class EventType(str, Enum):
    AGENT_RUN_STARTED = "agent_run_started"
    AGENT_RUN_FINISHED = "agent_run_finished"
    APPROVAL_RECEIVED = "approval_received"
    JOB_QUEUED = "job_queued"
    JOB_STARTED = "job_started"
    JOB_FINISHED = "job_finished"


@dataclass(slots=True)
class OrchestratorEvent:
    type: EventType
    project_slug: str
    payload: dict[str, Any]
    created_at: str


@dataclass(slots=True)
class ProjectRecord:
    slug: str
    project_dir: str
    name: str
    description: str
    repo: str
    tech_stack: str
    executor: str
    base_branch: str
    current_branch: str
    phase: str
    status: str
    spec: dict[str, Any]
    workflow: dict[str, Any]
    approvals: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    archived_at: str | None


@dataclass(slots=True)
class AgentRunRecord:
    id: str
    project_slug: str
    job_id: str | None
    agent_name: str
    skill: str
    trigger: str
    command: list[str]
    status: str
    pid: int | None
    exit_code: int | None
    started_at: str
    finished_at: str | None
    stdout_tail: str | None
    stderr_tail: str | None
    metadata: dict[str, Any]


@dataclass(slots=True)
class ApprovalRecord:
    id: str
    project_slug: str
    phase: str
    source: str
    state: str
    received_at: str
    payload: dict[str, Any]


@dataclass(slots=True)
class JobRecord:
    id: str
    project_slug: str
    job_type: str
    skill: str
    trigger: str
    status: str
    agent_name: str | None
    requested_agent: str | None
    run_id: str | None
    allow_fallback: bool
    payload: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: str | None


@dataclass(slots=True)
class WorkflowEventRecord:
    id: str
    project_slug: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


@dataclass(slots=True)
class ProjectSourceRecord:
    project_slug: str
    source_type: str
    label: str
    location: str
    content_text: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ProjectRepoBindingRecord:
    project_slug: str
    provider: str
    mode: str
    repo_name: str
    repo_url: str
    local_path: str
    is_new: bool
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class BackendStore:
    def __init__(self, db_path: Path | None = None):
        self.path = (db_path or DATABASE_PATH).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _managed_conn(self) -> Iterable[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._managed_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    slug TEXT PRIMARY KEY,
                    project_dir TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    repo TEXT NOT NULL DEFAULT '',
                    tech_stack TEXT NOT NULL DEFAULT '',
                    executor TEXT NOT NULL DEFAULT '',
                    base_branch TEXT NOT NULL DEFAULT '',
                    current_branch TEXT NOT NULL DEFAULT '',
                    phase TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    spec_json TEXT NOT NULL DEFAULT '{}',
                    workflow_json TEXT NOT NULL DEFAULT '{}',
                    approvals_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived_at TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    job_id TEXT,
                    agent_name TEXT NOT NULL,
                    skill TEXT NOT NULL,
                    trigger_source TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pid INTEGER,
                    exit_code INTEGER,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    stdout_tail TEXT,
                    stderr_tail TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    skill TEXT NOT NULL DEFAULT '',
                    trigger_source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_name TEXT,
                    requested_agent TEXT,
                    run_id TEXT,
                    allow_fallback INTEGER NOT NULL DEFAULT 1,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT,
                    FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS approval_events (
                    id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    source TEXT NOT NULL,
                    state TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS workflow_events (
                    id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_sources (
                    project_slug TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    content_text TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS project_repo_bindings (
                    project_slug TEXT PRIMARY KEY,
                    provider TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    repo_name TEXT NOT NULL DEFAULT '',
                    repo_url TEXT NOT NULL DEFAULT '',
                    local_path TEXT NOT NULL DEFAULT '',
                    is_new INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_runs_project ON agent_runs(project_slug, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_slug, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_workflow_events_project ON workflow_events(project_slug, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_approval_events_project ON approval_events(project_slug, received_at DESC);
                """
            )
            self._ensure_column(conn, "projects", "spec_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "projects", "workflow_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "agent_runs", "job_id", "TEXT")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def sync_project(
        self,
        project_dir: Path,
        *,
        spec: dict[str, Any] | None = None,
        workflow_data: dict[str, Any] | None = None,
        archived_at: str | None = None,
    ) -> ProjectRecord:
        resolved_dir = project_dir.resolve()
        slug = project_slug(resolved_dir)
        spec = spec if spec is not None else MemoryManager(resolved_dir).spec()
        workflow_data = workflow_data or {}
        approvals = spec.get("phase_approvals", {})
        now = _utcnow()

        payload = {
            "slug": slug,
            "project_dir": str(resolved_dir),
            "name": spec.get("project_name") or resolved_dir.name,
            "description": spec.get("description") or "",
            "repo": spec.get("repo") or "",
            "tech_stack": spec.get("tech_stack") or "",
            "executor": spec.get("executor") or "",
            "base_branch": workflow_data.get("base_branch") or "main",
            "current_branch": workflow_data.get("current_branch") or "main",
            "phase": workflow_data.get("phase") or "",
            "status": workflow_data.get("status") or "",
            "spec_json": _json(spec),
            "workflow_json": _json(workflow_data),
            "approvals_json": _json(approvals),
            "metadata_json": _json(
                {
                    "agent_fallback": bool(spec.get("agent_fallback", True)),
                    "slack_webhook": bool(spec.get("slack_webhook")),
                }
            ),
            "updated_at": now,
            "archived_at": archived_at,
        }

        with self._managed_conn() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    slug, project_dir, name, description, repo, tech_stack, executor,
                    base_branch, current_branch, phase, status, spec_json, workflow_json, approvals_json,
                    metadata_json, created_at, updated_at, archived_at
                ) VALUES (
                    :slug, :project_dir, :name, :description, :repo, :tech_stack, :executor,
                    :base_branch, :current_branch, :phase, :status, :spec_json, :workflow_json, :approvals_json,
                    :metadata_json, :updated_at, :updated_at, :archived_at
                )
                ON CONFLICT(slug) DO UPDATE SET
                    project_dir = excluded.project_dir,
                    name = excluded.name,
                    description = excluded.description,
                    repo = excluded.repo,
                    tech_stack = excluded.tech_stack,
                    executor = excluded.executor,
                    base_branch = excluded.base_branch,
                    current_branch = excluded.current_branch,
                    phase = excluded.phase,
                    status = excluded.status,
                    spec_json = excluded.spec_json,
                    workflow_json = excluded.workflow_json,
                    approvals_json = excluded.approvals_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at,
                    archived_at = excluded.archived_at
                """,
                payload,
            )
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        return self._project_from_row(row)

    def archive_project(self, project_dir: Path) -> ProjectRecord:
        return self.sync_project(project_dir, archived_at=_utcnow())

    def get_project(self, slug: str) -> ProjectRecord | None:
        with self._managed_conn() as conn:
            row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
        return self._project_from_row(row) if row else None

    def list_projects(self) -> list[ProjectRecord]:
        with self._managed_conn() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [self._project_from_row(row) for row in rows]

    def record_agent_run(
        self,
        *,
        project_slug: str,
        job_id: str | None,
        agent_name: str,
        skill: str,
        trigger: str,
        command: list[str],
        pid: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        run_id = uuid.uuid4().hex
        started_at = _utcnow()
        payload = {
            "id": run_id,
            "project_slug": project_slug,
            "job_id": job_id,
            "agent_name": agent_name,
            "skill": skill,
            "trigger_source": trigger,
            "command_json": _json(command),
            "status": "running",
            "pid": pid,
            "exit_code": None,
            "started_at": started_at,
            "finished_at": None,
            "stdout_tail": None,
            "stderr_tail": None,
            "metadata_json": _json(metadata),
        }
        with self._managed_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                    id, project_slug, job_id, agent_name, skill, trigger_source, command_json,
                    status, pid, exit_code, started_at, finished_at, stdout_tail,
                    stderr_tail, metadata_json
                ) VALUES (
                    :id, :project_slug, :job_id, :agent_name, :skill, :trigger_source, :command_json,
                    :status, :pid, :exit_code, :started_at, :finished_at, :stdout_tail,
                    :stderr_tail, :metadata_json
                )
                """,
                payload,
            )
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_from_row(row)

    def finish_agent_run(
        self,
        run_id: str,
        *,
        exit_code: int,
        stdout: str | None,
        stderr: str | None,
    ) -> AgentRunRecord:
        finished_at = _utcnow()
        status = "succeeded" if exit_code == 0 else "failed"
        with self._managed_conn() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, exit_code = ?, finished_at = ?, stdout_tail = ?, stderr_tail = ?
                WHERE id = ?
                """,
                (status, exit_code, finished_at, _tail(stdout), _tail(stderr), run_id),
            )
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_from_row(row)

    def recent_agent_runs(self, project_slug: str, limit: int = 20) -> list[AgentRunRecord]:
        with self._managed_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM agent_runs WHERE project_slug = ? ORDER BY started_at DESC LIMIT ?",
                (project_slug, limit),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def get_agent_run(self, run_id: str) -> AgentRunRecord | None:
        with self._managed_conn() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_from_row(row) if row else None

    def record_approval(
        self,
        *,
        project_slug: str,
        phase: str,
        source: str,
        state: str,
        payload: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        approval_id = uuid.uuid4().hex
        received_at = _utcnow()
        row_payload = {
            "id": approval_id,
            "project_slug": project_slug,
            "phase": phase,
            "source": source,
            "state": state,
            "received_at": received_at,
            "payload_json": _json(payload),
        }
        with self._managed_conn() as conn:
            conn.execute(
                """
                INSERT INTO approval_events (
                    id, project_slug, phase, source, state, received_at, payload_json
                ) VALUES (
                    :id, :project_slug, :phase, :source, :state, :received_at, :payload_json
                )
                """,
                row_payload,
            )
            row = conn.execute("SELECT * FROM approval_events WHERE id = ?", (approval_id,)).fetchone()
        return self._approval_from_row(row)

    def create_job(
        self,
        *,
        project_slug: str,
        job_type: str,
        skill: str,
        trigger: str,
        requested_agent: str | None,
        allow_fallback: bool,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        job_id = uuid.uuid4().hex
        created_at = _utcnow()
        row_payload = {
            "id": job_id,
            "project_slug": project_slug,
            "job_type": job_type,
            "skill": skill,
            "trigger_source": trigger,
            "status": "queued",
            "agent_name": None,
            "requested_agent": requested_agent,
            "run_id": None,
            "allow_fallback": 1 if allow_fallback else 0,
            "payload_json": _json(payload),
            "metadata_json": _json(metadata),
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
        with self._managed_conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, project_slug, job_type, skill, trigger_source, status, agent_name,
                    requested_agent, run_id, allow_fallback, payload_json, metadata_json,
                    created_at, started_at, finished_at, error
                ) VALUES (
                    :id, :project_slug, :job_type, :skill, :trigger_source, :status, :agent_name,
                    :requested_agent, :run_id, :allow_fallback, :payload_json, :metadata_json,
                    :created_at, :started_at, :finished_at, :error
                )
                """,
                row_payload,
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def start_job(
        self,
        job_id: str,
        *,
        agent_name: str,
        run_id: str,
    ) -> JobRecord:
        started_at = _utcnow()
        with self._managed_conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, agent_name = ?, run_id = ?, started_at = ?, error = NULL
                WHERE id = ?
                """,
                ("running", agent_name, run_id, started_at, job_id),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def finish_job(
        self,
        job_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> JobRecord:
        finished_at = _utcnow()
        with self._managed_conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?, error = ?
                WHERE id = ?
                """,
                (status, finished_at, error, job_id),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row)

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._managed_conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def recent_jobs(self, project_slug: str, limit: int = 20) -> list[JobRecord]:
        with self._managed_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE project_slug = ? ORDER BY created_at DESC LIMIT ?",
                (project_slug, limit),
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def record_workflow_event(
        self,
        *,
        project_slug: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> WorkflowEventRecord:
        event_id = uuid.uuid4().hex
        timestamp = created_at or _utcnow()
        row_payload = {
            "id": event_id,
            "project_slug": project_slug,
            "event_type": event_type,
            "payload_json": _json(payload),
            "created_at": timestamp,
        }
        with self._managed_conn() as conn:
            conn.execute(
                """
                INSERT INTO workflow_events (
                    id, project_slug, event_type, payload_json, created_at
                ) VALUES (
                    :id, :project_slug, :event_type, :payload_json, :created_at
                )
                """,
                row_payload,
            )
            row = conn.execute("SELECT * FROM workflow_events WHERE id = ?", (event_id,)).fetchone()
        return self._workflow_event_from_row(row)

    def recent_workflow_events(self, project_slug: str, limit: int = 50) -> list[WorkflowEventRecord]:
        with self._managed_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_events WHERE project_slug = ? ORDER BY created_at DESC LIMIT ?",
                (project_slug, limit),
            ).fetchall()
        return [self._workflow_event_from_row(row) for row in rows]

    def upsert_project_source(
        self,
        *,
        project_slug: str,
        source_type: str,
        label: str = "",
        location: str = "",
        content_text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> ProjectSourceRecord:
        now = _utcnow()
        row_payload = {
            "project_slug": project_slug,
            "source_type": source_type,
            "label": label,
            "location": location,
            "content_text": content_text,
            "metadata_json": _json(metadata),
            "created_at": now,
            "updated_at": now,
        }
        with self._managed_conn() as conn:
            conn.execute(
                """
                INSERT INTO project_sources (
                    project_slug, source_type, label, location, content_text,
                    metadata_json, created_at, updated_at
                ) VALUES (
                    :project_slug, :source_type, :label, :location, :content_text,
                    :metadata_json, :created_at, :updated_at
                )
                ON CONFLICT(project_slug) DO UPDATE SET
                    source_type = excluded.source_type,
                    label = excluded.label,
                    location = excluded.location,
                    content_text = excluded.content_text,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                row_payload,
            )
            row = conn.execute(
                "SELECT * FROM project_sources WHERE project_slug = ?",
                (project_slug,),
            ).fetchone()
        return self._project_source_from_row(row)

    def get_project_source(self, project_slug: str) -> ProjectSourceRecord | None:
        with self._managed_conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_sources WHERE project_slug = ?",
                (project_slug,),
            ).fetchone()
        return self._project_source_from_row(row) if row else None

    def upsert_project_repo_binding(
        self,
        *,
        project_slug: str,
        provider: str = "",
        mode: str = "",
        repo_name: str = "",
        repo_url: str = "",
        local_path: str = "",
        is_new: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> ProjectRepoBindingRecord:
        now = _utcnow()
        row_payload = {
            "project_slug": project_slug,
            "provider": provider,
            "mode": mode,
            "repo_name": repo_name,
            "repo_url": repo_url,
            "local_path": local_path,
            "is_new": 1 if is_new else 0,
            "metadata_json": _json(metadata),
            "created_at": now,
            "updated_at": now,
        }
        with self._managed_conn() as conn:
            conn.execute(
                """
                INSERT INTO project_repo_bindings (
                    project_slug, provider, mode, repo_name, repo_url, local_path,
                    is_new, metadata_json, created_at, updated_at
                ) VALUES (
                    :project_slug, :provider, :mode, :repo_name, :repo_url, :local_path,
                    :is_new, :metadata_json, :created_at, :updated_at
                )
                ON CONFLICT(project_slug) DO UPDATE SET
                    provider = excluded.provider,
                    mode = excluded.mode,
                    repo_name = excluded.repo_name,
                    repo_url = excluded.repo_url,
                    local_path = excluded.local_path,
                    is_new = excluded.is_new,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                row_payload,
            )
            row = conn.execute(
                "SELECT * FROM project_repo_bindings WHERE project_slug = ?",
                (project_slug,),
            ).fetchone()
        return self._project_repo_binding_from_row(row)

    def get_project_repo_binding(self, project_slug: str) -> ProjectRepoBindingRecord | None:
        with self._managed_conn() as conn:
            row = conn.execute(
                "SELECT * FROM project_repo_bindings WHERE project_slug = ?",
                (project_slug,),
            ).fetchone()
        return self._project_repo_binding_from_row(row) if row else None

    def _project_from_row(self, row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            slug=row["slug"],
            project_dir=row["project_dir"],
            name=row["name"],
            description=row["description"],
            repo=row["repo"],
            tech_stack=row["tech_stack"],
            executor=row["executor"],
            base_branch=row["base_branch"],
            current_branch=row["current_branch"],
            phase=row["phase"],
            status=row["status"],
            spec=json.loads(row["spec_json"]),
            workflow=json.loads(row["workflow_json"]),
            approvals=json.loads(row["approvals_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
        )

    def _run_from_row(self, row: sqlite3.Row) -> AgentRunRecord:
        return AgentRunRecord(
            id=row["id"],
            project_slug=row["project_slug"],
            job_id=row["job_id"],
            agent_name=row["agent_name"],
            skill=row["skill"],
            trigger=row["trigger_source"],
            command=json.loads(row["command_json"]),
            status=row["status"],
            pid=row["pid"],
            exit_code=row["exit_code"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            stdout_tail=row["stdout_tail"],
            stderr_tail=row["stderr_tail"],
            metadata=json.loads(row["metadata_json"]),
        )

    def _approval_from_row(self, row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            id=row["id"],
            project_slug=row["project_slug"],
            phase=row["phase"],
            source=row["source"],
            state=row["state"],
            received_at=row["received_at"],
            payload=json.loads(row["payload_json"]),
        )

    def _job_from_row(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            project_slug=row["project_slug"],
            job_type=row["job_type"],
            skill=row["skill"],
            trigger=row["trigger_source"],
            status=row["status"],
            agent_name=row["agent_name"],
            requested_agent=row["requested_agent"],
            run_id=row["run_id"],
            allow_fallback=bool(row["allow_fallback"]),
            payload=json.loads(row["payload_json"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
        )

    def _workflow_event_from_row(self, row: sqlite3.Row) -> WorkflowEventRecord:
        return WorkflowEventRecord(
            id=row["id"],
            project_slug=row["project_slug"],
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
        )

    def _project_source_from_row(self, row: sqlite3.Row) -> ProjectSourceRecord:
        return ProjectSourceRecord(
            project_slug=row["project_slug"],
            source_type=row["source_type"],
            label=row["label"],
            location=row["location"],
            content_text=row["content_text"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _project_repo_binding_from_row(self, row: sqlite3.Row) -> ProjectRepoBindingRecord:
        return ProjectRepoBindingRecord(
            project_slug=row["project_slug"],
            provider=row["provider"],
            mode=row["mode"],
            repo_name=row["repo_name"],
            repo_url=row["repo_url"],
            local_path=row["local_path"],
            is_new=bool(row["is_new"]),
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[OrchestratorEvent], None]] = []

    def subscribe(self, handler: Callable[[OrchestratorEvent], None]) -> None:
        with self._lock:
            self._subscribers.append(handler)

    def publish(self, event: OrchestratorEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for handler in subscribers:
            handler(event)


class OrchestratorRuntime:
    def __init__(self, store: BackendStore | None = None, events: EventBus | None = None):
        self.store = store or BackendStore()
        self.events = events or EventBus()
        self._threads: dict[str, threading.Thread] = {}

    def subscribe(self, handler: Callable[[OrchestratorEvent], None]) -> None:
        self.events.subscribe(handler)

    def sync_project(
        self,
        project_dir: Path,
        *,
        spec: dict[str, Any] | None = None,
        workflow_data: dict[str, Any] | None = None,
    ) -> ProjectRecord:
        return self.store.sync_project(project_dir, spec=spec, workflow_data=workflow_data)

    def archive_project(self, project_dir: Path) -> ProjectRecord:
        return self.store.archive_project(project_dir)

    def _publish_event(
        self,
        *,
        event_type: EventType,
        project_slug: str,
        payload: dict[str, Any],
        created_at: str | None = None,
    ) -> OrchestratorEvent:
        event = self.store.record_workflow_event(
            project_slug=project_slug,
            event_type=event_type.value,
            payload=payload,
            created_at=created_at,
        )
        orchestrator_event = OrchestratorEvent(
            type=event_type,
            project_slug=project_slug,
            payload=payload,
            created_at=event.created_at,
        )
        self.events.publish(orchestrator_event)
        return orchestrator_event

    def _candidate_agents(
        self,
        project_dir: Path,
        *,
        requested: str | None,
        allow_fallback: bool,
    ) -> list[str]:
        registry = AgentRegistry(project_dir)
        candidates: list[str] = []
        if requested:
            candidates.append(requested)
        if allow_fallback:
            for agent in registry.get_available_agents():
                if agent.name not in candidates:
                    candidates.append(agent.name)
        return candidates

    def _resolve_job_execution(
        self,
        project_dir: Path,
        *,
        job_type: str,
        skill: str | None,
        preferred_agent: str | None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[str, str, str | None]:
        from sdlc_orchestrator.memory import DEFAULT_EXECUTOR

        spec = MemoryManager(project_dir).spec()
        normalized_job_type = (job_type or "").strip().lower().replace("-", "_") or "requirement"
        phase_name = str((payload or {}).get("phase") or normalized_job_type).strip().lower().replace("-", "_")
        selected_skill = skill or resolve_phase_skill(phase_name or normalized_job_type)

        configured_agents = spec.get("job_agents") or {}
        requested_agent = (
            preferred_agent
            or configured_agents.get(normalized_job_type)
            or configured_agents.get(phase_name)
            or spec.get("executor")
            or DEFAULT_EXECUTOR
        )
        return normalized_job_type, selected_skill, requested_agent

    def queue_job(
        self,
        project_dir: Path,
        *,
        job_type: str,
        skill: str | None = None,
        preferred_agent: str | None = None,
        trigger: str = "manual",
        allow_fallback: bool = True,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobRecord:
        project_dir = project_dir.resolve()
        slug = project_slug(project_dir)
        workflow = self.store.get_project(slug)
        resolved_job_type, resolved_skill, requested = self._resolve_job_execution(
            project_dir,
            job_type=job_type,
            skill=skill,
            preferred_agent=preferred_agent,
            payload={
                "phase": workflow.phase if workflow else "",
                **(payload or {}),
            },
        )
        job_payload = {
            "phase": workflow.phase if workflow else "",
            "status": workflow.status if workflow else "",
            **(payload or {}),
        }
        job = self.store.create_job(
            project_slug=slug,
            job_type=resolved_job_type,
            skill=resolved_skill,
            trigger=trigger,
            requested_agent=requested,
            allow_fallback=allow_fallback,
            payload=job_payload,
            metadata={
                "explicit_skill": skill,
                "dispatched_skill": resolved_skill,
                **(metadata or {}),
            },
        )
        self._publish_event(
            event_type=EventType.JOB_QUEUED,
            project_slug=slug,
            payload={
                "job_id": job.id,
                "job_type": job.job_type,
                "skill": job.skill,
                "trigger": job.trigger,
                "requested_agent": job.requested_agent,
                "payload": job.payload,
            },
            created_at=job.created_at,
        )
        return job

    def dispatch_job(self, project_dir: Path, job_id: str) -> AgentRunRecord | None:
        project_dir = project_dir.resolve()
        job = self.store.get_job(job_id)
        if not job:
            return None

        candidates = self._candidate_agents(
            project_dir,
            requested=job.requested_agent,
            allow_fallback=job.allow_fallback,
        )
        for agent_name in candidates:
            command = _build_executor_command(agent_name, job.skill)
            if not command:
                continue
            try:
                run = self.spawn_agent(
                    project_dir=project_dir,
                    job_id=job.id,
                    agent_name=agent_name,
                    skill=job.skill,
                    command=command,
                    trigger=job.trigger,
                    metadata={
                        "job_id": job.id,
                        "job_type": job.job_type,
                        "requested_agent": job.requested_agent,
                        "allow_fallback": job.allow_fallback,
                        **job.metadata,
                    },
                )
                started = self.store.start_job(job.id, agent_name=agent_name, run_id=run.id)
                self._publish_event(
                    event_type=EventType.JOB_STARTED,
                    project_slug=job.project_slug,
                    payload={
                        "job_id": started.id,
                        "job_type": started.job_type,
                        "run_id": started.run_id,
                        "agent_name": started.agent_name,
                    },
                    created_at=started.started_at,
                )
                return run
            except OSError:
                continue

        failed = self.store.finish_job(job.id, status="failed", error="No runnable headless agent found")
        self._publish_event(
            event_type=EventType.JOB_FINISHED,
            project_slug=failed.project_slug,
            payload={
                "job_id": failed.id,
                "job_type": failed.job_type,
                "status": failed.status,
                "error": failed.error,
            },
            created_at=failed.finished_at,
        )
        return None

    def record_approval(
        self,
        project_dir: Path,
        *,
        phase: str,
        source: str,
        state: str,
        payload: dict[str, Any] | None = None,
    ) -> ApprovalRecord:
        slug = project_slug(project_dir.resolve())
        record = self.store.record_approval(
            project_slug=slug,
            phase=phase,
            source=source,
            state=state,
            payload=payload,
        )
        self._publish_event(
            event_type=EventType.APPROVAL_RECEIVED,
            project_slug=slug,
            payload={
                "approval_id": record.id,
                "phase": record.phase,
                "source": record.source,
                "state": record.state,
                "payload": record.payload,
            },
            created_at=record.received_at,
        )
        return record

    def spawn_agent(
        self,
        *,
        project_dir: Path,
        job_id: str | None = None,
        agent_name: str,
        skill: str,
        command: list[str],
        trigger: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        slug = project_slug(project_dir.resolve())
        proc = subprocess.Popen(
            command,
            cwd=str(project_dir.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            record = self.store.record_agent_run(
                project_slug=slug,
                job_id=job_id,
                agent_name=agent_name,
                skill=skill,
                trigger=trigger,
                command=command,
                pid=proc.pid,
                metadata=metadata,
            )
        except Exception:
            proc.kill()
            proc.communicate()
            raise
        self._publish_event(
            event_type=EventType.AGENT_RUN_STARTED,
            project_slug=slug,
            payload={
                "run_id": record.id,
                "job_id": job_id,
                "agent_name": agent_name,
                "skill": skill,
                "trigger": trigger,
                "pid": proc.pid,
            },
            created_at=record.started_at,
        )

        thread = threading.Thread(
            target=self._wait_for_process,
            args=(record.id, job_id, slug, agent_name, skill, proc),
            daemon=True,
        )
        thread.start()
        self._threads[record.id] = thread
        return record

    def spawn_for_project(
        self,
        project_dir: Path,
        *,
        job_type: str | None = None,
        skill: str | None = None,
        preferred_agent: str | None = None,
        trigger: str = "manual",
        allow_fallback: bool = True,
        payload: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRunRecord | None:
        project_dir = project_dir.resolve()
        project = self.store.get_project(project_slug(project_dir))
        queued = self.queue_job(
            project_dir,
            job_type=job_type or (project.phase if project and project.phase else "requirement"),
            skill=skill,
            preferred_agent=preferred_agent,
            trigger=trigger,
            allow_fallback=allow_fallback,
            payload=payload,
            metadata=metadata,
        )
        return self.dispatch_job(project_dir, queued.id)

    def _wait_for_process(
        self,
        run_id: str,
        job_id: str | None,
        slug: str,
        agent_name: str,
        skill: str,
        proc: subprocess.Popen[str],
    ) -> None:
        stdout, stderr = proc.communicate()
        record = self.store.finish_agent_run(
            run_id,
            exit_code=proc.returncode or 0,
            stdout=stdout,
            stderr=stderr,
        )
        self._publish_event(
            event_type=EventType.AGENT_RUN_FINISHED,
            project_slug=slug,
            payload={
                "run_id": run_id,
                "job_id": job_id,
                "agent_name": agent_name,
                "skill": skill,
                "exit_code": record.exit_code,
                "status": record.status,
                "stdout_tail": record.stdout_tail,
                "stderr_tail": record.stderr_tail,
            },
            created_at=record.finished_at or _utcnow(),
        )
        if job_id:
            job_error = None
            if record.status != "succeeded":
                job_error = record.stderr_tail or record.stdout_tail
            job = self.store.finish_job(job_id, status=record.status, error=job_error)
            self._publish_event(
                event_type=EventType.JOB_FINISHED,
                project_slug=slug,
                payload={
                    "job_id": job.id,
                    "job_type": job.job_type,
                    "run_id": run_id,
                    "agent_name": agent_name,
                    "status": job.status,
                    "error": job.error,
                },
                created_at=job.finished_at,
            )
        self._threads.pop(run_id, None)


class WorkflowRepository:
    def __init__(self, runtime: OrchestratorRuntime | None = None):
        self.runtime = runtime or get_runtime()

    def load(self, project_dir: Path):
        from sdlc_orchestrator.state_machine import WorkflowState

        return WorkflowState(project_dir.resolve())

    def save(self, workflow) -> None:
        workflow.save()


class WorkflowService:
    def __init__(self, repository: WorkflowRepository | None = None):
        self.repository = repository or WorkflowRepository()

    def load(self, project_dir: Path):
        return self.repository.load(project_dir)

    def initialize(self, project_dir: Path, phase) :
        from sdlc_orchestrator.state_machine import Status

        wf = self.repository.load(project_dir)
        if not wf._data.get("history") and wf.phase == phase and wf.status == Status.IN_PROGRESS:
            self.repository.save(wf)
            return wf
        wf.transition_to(phase, Status.IN_PROGRESS)
        return wf

    def transition_to(self, project_dir: Path, phase, status) :
        wf = self.repository.load(project_dir)
        wf.transition_to(phase, status)
        return wf

    def set_status(self, project_dir: Path, status) :
        from sdlc_orchestrator.state_machine import Status

        wf = self.repository.load(project_dir)
        if status == Status.AWAITING_APPROVAL:
            wf.submit_for_approval()
        elif status == Status.IN_PROGRESS and wf.status == Status.BLOCKED:
            wf.unblock()
        elif status == Status.DONE:
            wf.mark_done()
        else:
            wf.set_status(status)
        return wf

    def set_blocked(self, project_dir: Path, reason: str):
        wf = self.repository.load(project_dir)
        wf.set_blocked(reason)
        return wf

    def unblock(self, project_dir: Path):
        wf = self.repository.load(project_dir)
        wf.unblock()
        return wf

    def approve(self, project_dir: Path):
        wf = self.repository.load(project_dir)
        wf.approve()
        return wf

    def mark_done(self, project_dir: Path):
        wf = self.repository.load(project_dir)
        wf.mark_done()
        return wf

    def set_branches(
        self,
        project_dir: Path,
        *,
        base_branch: str | None = None,
        current_branch: str | None = None,
    ):
        wf = self.repository.load(project_dir)
        wf.set_branches(base_branch=base_branch, current_branch=current_branch)
        return wf

    def set_value(
        self,
        project_dir: Path,
        value: str,
        *,
        blocked_reason: str = "manually set via CLI",
    ):
        from sdlc_orchestrator.state_machine import Phase, Status, _LEGACY_MAP

        if value == "blocked":
            return self.set_blocked(project_dir, blocked_reason)

        if value in _LEGACY_MAP:
            phase, status = _LEGACY_MAP[value]
            if phase == Phase.DONE or value == "done":
                return self.mark_done(project_dir)
            if phase is not None:
                return self.transition_to(project_dir, phase, status)
            if status == Status.BLOCKED:
                return self.set_blocked(project_dir, blocked_reason)
            return self.set_status(project_dir, status)

        try:
            phase = Phase(value)
        except ValueError:
            phase = None
        if phase is not None:
            return self.initialize(project_dir, phase)

        try:
            status = Status(value)
        except ValueError as exc:
            raise ValueError(value) from exc
        if status == Status.BLOCKED:
            return self.set_blocked(project_dir, blocked_reason)
        return self.set_status(project_dir, status)


_runtime_lock = threading.Lock()
_default_runtime: OrchestratorRuntime | None = None


def _register_default_handlers(runtime: OrchestratorRuntime) -> None:
    if getattr(runtime, "_default_handlers_registered", False):
        return

    def _handle(event: OrchestratorEvent) -> None:
        if event.type == EventType.APPROVAL_RECEIVED:
            record = runtime.store.get_project(event.project_slug)
            if not record or record.archived_at:
                return
            runtime.spawn_for_project(
                Path(record.project_dir),
                job_type=record.phase or "approval_followup",
                preferred_agent=record.executor or None,
                trigger=event.type.value,
                allow_fallback=bool(record.metadata.get("agent_fallback", True)),
                payload={
                    "phase": record.phase,
                    "status": record.status,
                    "approval_state": event.payload.get("state"),
                },
                metadata={"approval": event.payload},
            )
            return

        if event.type != EventType.AGENT_RUN_FINISHED:
            return

        record = runtime.store.get_project(event.project_slug)
        if not record:
            return
        registry = AgentRegistry(Path(record.project_dir))
        agent_name = str(event.payload.get("agent_name") or "")
        stderr = str(event.payload.get("stderr_tail") or "")
        stdout = str(event.payload.get("stdout_tail") or "")
        combined = "\n".join(part for part in (stderr, stdout) if part).strip()
        exit_code = int(event.payload.get("exit_code") or 0)

        if exit_code == 0:
            registry.mark_agent_used(agent_name, success=True)
            registry.record_event(
                "runtime_execution_success",
                f"{agent_name} completed {event.payload.get('skill', 'run')}",
                agent=agent_name,
                metadata={"run_id": event.payload.get("run_id")},
            )
            return

        if registry.is_credit_error(combined):
            registry.set_agent_status(
                agent_name,
                AgentStatus.NO_CREDITS,
                f"Credit exhausted: {combined[:200]}",
                health_reason="credit or quota exhausted",
                last_credit_error=combined[:500],
            )
            registry.record_event(
                "runtime_credit_exhausted",
                f"{agent_name} hit a credit or quota limit",
                agent=agent_name,
                metadata={
                    "run_id": event.payload.get("run_id"),
                    "error": combined[:500],
                },
            )
            return

        registry.mark_agent_used(agent_name, success=False)
        registry.record_event(
            "runtime_execution_failed",
            f"{agent_name} failed while executing {event.payload.get('skill', 'run')}",
            agent=agent_name,
            metadata={
                "run_id": event.payload.get("run_id"),
                "error": combined[:500],
            },
        )

    runtime.subscribe(_handle)
    runtime._default_handlers_registered = True


def get_runtime() -> OrchestratorRuntime:
    global _default_runtime
    with _runtime_lock:
        if _default_runtime is None:
            _default_runtime = OrchestratorRuntime()
        _register_default_handlers(_default_runtime)
        return _default_runtime


def get_workflow_service() -> WorkflowService:
    return WorkflowService(WorkflowRepository(get_runtime()))


def sync_project_from_disk(project_dir: Path, workflow_data: dict[str, Any] | None = None) -> ProjectRecord:
    from sdlc_orchestrator.state_machine import WorkflowState

    if workflow_data is None:
        try:
            workflow_data = WorkflowState(project_dir)._data
        except Exception:
            workflow_data = {}
    return get_runtime().sync_project(
        project_dir,
        spec=MemoryManager(project_dir).spec(),
        workflow_data=workflow_data,
    )


def record_approval_event(
    project_dir: Path,
    *,
    phase: str,
    source: str,
    state: str,
    payload: dict[str, Any] | None = None,
) -> ApprovalRecord:
    return get_runtime().record_approval(
        project_dir,
        phase=phase,
        source=source,
        state=state,
        payload=payload,
    )


def _build_executor_command(agent_name: str, skill: str) -> list[str] | None:
    from sdlc_orchestrator.memory import EXECUTOR_CLI, executor_config

    cmd_template = EXECUTOR_CLI.get(agent_name)
    if not cmd_template:
        return None
    if agent_name == "codex":
        _, skills_dir, _ = executor_config(agent_name)
        skill_file = skills_dir / f"{skill}.md"
        prompt = skill_file.read_text() if skill_file.exists() else skill
        return [part.replace("{skill}", prompt) for part in cmd_template]
    return [part.replace("{skill}", skill) for part in cmd_template]

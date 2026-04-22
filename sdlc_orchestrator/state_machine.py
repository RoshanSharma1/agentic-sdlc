"""
State machine: phases → stories → tasks → done.

Hierarchy:
  phase (requirement | design | planning | implementation | testing | documentation | done)
    └── status (pending | in_progress | awaiting_approval | blocked | done)
        └── stories  (ALL phases have stories)
              single-artifact phases: one story keyed by phase name
              implementation: N stories keyed STORY-NNN
              └── story
                    ├── status (pending | in_progress | awaiting_review | feedback | done)
                    ├── github_issue  (int, optional)
                    ├── github_pr     (int, optional)
                    └── tasks  (implementation stories only — TASK-NNN → status)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from sdlc_orchestrator.utils import sdlc_home


class Phase(str, Enum):
    REQUIREMENT    = "requirement"
    DESIGN         = "design"
    PLANNING       = "planning"
    IMPLEMENTATION = "implementation"
    TESTING        = "testing"
    DOCUMENTATION  = "documentation"
    DONE           = "done"


class Status(str, Enum):
    PENDING           = "pending"
    IN_PROGRESS       = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED           = "blocked"
    DONE              = "done"


class StoryStatus(str, Enum):
    PENDING         = "pending"
    IN_PROGRESS     = "in_progress"
    AWAITING_REVIEW = "awaiting_review"
    FEEDBACK        = "feedback"
    DONE            = "done"


PHASE_ORDER = [
    Phase.REQUIREMENT,
    Phase.DESIGN,
    Phase.PLANNING,
    Phase.IMPLEMENTATION,
    Phase.TESTING,
    Phase.DOCUMENTATION,
]

# States where execution is paused waiting for human action
def _is_approval_gate(phase: Phase, status: Status) -> bool:
    return status in (Status.AWAITING_APPROVAL, Status.BLOCKED)

def _is_executable(phase: Phase, status: Status) -> bool:
    return phase != Phase.DONE and status == Status.IN_PROGRESS

# Human-readable labels
PHASE_LABELS: dict[Phase, str] = {
    Phase.REQUIREMENT:    "Requirements",
    Phase.DESIGN:         "Design",
    Phase.PLANNING:       "Planning",
    Phase.IMPLEMENTATION: "Implementation",
    Phase.TESTING:        "Testing",
    Phase.DOCUMENTATION:  "Documentation",
    Phase.DONE:           "Complete",
}

STATUS_LABELS: dict[Status, str] = {
    Status.PENDING:           "Pending",
    Status.IN_PROGRESS:       "In progress",
    Status.AWAITING_APPROVAL: "Awaiting approval",
    Status.BLOCKED:           "Blocked",
    Status.DONE:              "Done",
}

# Backward compat: old flat state strings imported by other modules
# Maps legacy state value → (Phase, Status)
_LEGACY_MAP: dict[str, tuple[Phase | None, Status]] = {
    "requirement_in_progress":         (Phase.REQUIREMENT,    Status.IN_PROGRESS),
    "requirement_ready_for_approval":  (Phase.REQUIREMENT,    Status.AWAITING_APPROVAL),
    "design_in_progress":              (Phase.DESIGN,         Status.IN_PROGRESS),
    "awaiting_design_approval":        (Phase.DESIGN,         Status.AWAITING_APPROVAL),
    "task_plan_in_progress":           (Phase.PLANNING,       Status.IN_PROGRESS),
    "task_plan_ready":                 (Phase.PLANNING,       Status.AWAITING_APPROVAL),
    "story_in_progress":               (Phase.IMPLEMENTATION, Status.IN_PROGRESS),
    "story_awaiting_review":           (Phase.IMPLEMENTATION, Status.AWAITING_APPROVAL),
    "feedback_incorporation":          (Phase.IMPLEMENTATION, Status.IN_PROGRESS),
    "testing_in_progress":             (Phase.TESTING,        Status.IN_PROGRESS),
    "testing_awaiting_approval":       (Phase.TESTING,        Status.AWAITING_APPROVAL),
    "documentation_in_progress":       (Phase.DOCUMENTATION,  Status.IN_PROGRESS),
    "documentation_awaiting_approval": (Phase.DOCUMENTATION,  Status.AWAITING_APPROVAL),
    "blocked":                         (None,                 Status.BLOCKED),
    "done":                            (Phase.DONE,           Status.DONE),
    # older migrations
    "draft_requirement":               (Phase.REQUIREMENT,    Status.IN_PROGRESS),
    "awaiting_requirement_answer":     (Phase.REQUIREMENT,    Status.IN_PROGRESS),
    "implementation_in_progress":      (Phase.IMPLEMENTATION, Status.IN_PROGRESS),
    "test_failure_loop":               (Phase.IMPLEMENTATION, Status.IN_PROGRESS),
    "awaiting_review":                 (Phase.IMPLEMENTATION, Status.AWAITING_APPROVAL),
}

# Keep these names importable for callers that reference them
APPROVAL_STATES = frozenset()   # use wf.is_approval_gate() instead
EXECUTABLE_STATES = frozenset() # use wf.is_executable() instead


class WorkflowState:
    """Loads, mutates, and persists workflow/state.json."""

    def __init__(self, project_dir: Path,
                 notifier: Optional[Callable[[Phase, str], None]] = None):
        self.project_dir = project_dir.resolve()
        self._notifier = notifier
        _home = sdlc_home(project_dir)
        self.path = _home / "workflow" / "state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self._migrate(data)
            return data
        return self._defaults()

    @staticmethod
    def _migrate(data: dict) -> None:
        """Migrate old flat-state and old github/process keys in-place."""
        # Clean up legacy github project board fields
        github = data.get("github", {})
        github.pop("project", None)
        github.pop("epic_issue", None)
        for old_key in ("github_project", "github_epic_issue", "github_project_id"):
            data.pop(old_key, None)
        if not github:
            data.pop("github", None)

        # process sub-object (from previous refactor)
        process = data.setdefault("process", {"pid": None, "last_tick": None, "held": False})
        for old_key, new_key in (
            ("loop_pid",       "pid"),
            ("last_tick_time", "last_tick"),
            ("held",           "held"),
        ):
            if old_key in data:
                process.setdefault(new_key, data.pop(old_key))

        data.pop("approval_needed", None)

        # Migrate flat "state" → phase + status + phases hierarchy
        if "phase" not in data and "state" in data:
            old_state = data.pop("state")
            mapped = _LEGACY_MAP.get(old_state)
            if mapped:
                phase, status = mapped
            else:
                phase, status = Phase.REQUIREMENT, Status.IN_PROGRESS

            data["phase"] = (phase or Phase.REQUIREMENT).value
            data["status"] = status.value

            phases = data.setdefault("phases", _default_phases())
            _backfill_phases(phases, phase, status, data)

        # Migrate old flat story fields into phases.implementation
        impl = data.setdefault("phases", _default_phases()).setdefault(
            Phase.IMPLEMENTATION.value, _default_impl_phase()
        )
        if "current_story" in data:
            impl["current_story"] = data.pop("current_story")
        if "completed_stories" in data:
            for sid in data.pop("completed_stories", []):
                impl.setdefault("stories", {}).setdefault(
                    sid, {"status": StoryStatus.DONE.value, "current_task": None, "tasks": {}}
                )

        # Ensure all phases have a stories dict
        phases = data.setdefault("phases", _default_phases())
        for p in PHASE_ORDER:
            phase_entry = phases.setdefault(p.value, {"status": Status.PENDING.value})
            phase_entry.setdefault("stories", {} if p != Phase.IMPLEMENTATION else phase_entry.get("stories", {}))

        # Migrate old github.story_items / task_items into phase stories
        old_story_items = data.get("github", {}).pop("story_items", None) or {}
        old_task_items  = data.get("github", {}).pop("task_items",  None) or {}
        if old_story_items or old_task_items:
            _migrate_github_items(phases, old_story_items, old_task_items)

    def save(self) -> None:
        self._data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps(self._data, indent=2))

    @staticmethod
    def _defaults() -> dict:
        return {
            "phase": Phase.REQUIREMENT.value,
            "status": Status.IN_PROGRESS.value,
            "blocked_reason": None,
            "retry_count": 0,
            "base_branch": "main",
            "current_branch": "main",
            "phases": _default_phases(),
            "artifacts": {
                "requirement_questions": None,
                "requirements": None,
                "test_cases": None,
                "design": None,
                "plan": None,
                "test_results": None,
                "test_report": None,
                "review_summary": None,
            },
            "history": [],
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "process": {
                "pid": None,
                "last_tick": None,
                "held": False,
            },
        }

    # ── accessors ────────────────────────────────────────────────────────────

    @property
    def phase(self) -> Phase:
        return Phase(self._data.get("phase", Phase.REQUIREMENT.value))

    @property
    def status(self) -> Status:
        return Status(self._data.get("status", Status.IN_PROGRESS.value))

    @property
    def retry_count(self) -> int:
        return self._data.get("retry_count", 0)

    @property
    def blocked_reason(self) -> Optional[str]:
        return self._data.get("blocked_reason")

    @property
    def artifacts(self) -> dict:
        return self._data.get("artifacts", {})

    @property
    def _impl(self) -> dict:
        return self._data["phases"].setdefault(
            Phase.IMPLEMENTATION.value, _default_impl_phase()
        )

    @property
    def current_story(self) -> Optional[str]:
        return self._impl.get("current_story")

    @property
    def story_status(self) -> Optional[StoryStatus]:
        sid = self.current_story
        if not sid:
            return None
        story = self._impl.get("stories", {}).get(sid, {})
        raw = story.get("status")
        return StoryStatus(raw) if raw else None

    @property
    def current_task(self) -> Optional[str]:
        sid = self.current_story
        if not sid:
            return None
        return self._impl.get("stories", {}).get(sid, {}).get("current_task")

    @property
    def completed_stories(self) -> list[str]:
        return [
            sid for sid, s in self._impl.get("stories", {}).items()
            if s.get("status") == StoryStatus.DONE.value
        ]

    @property
    def _process(self) -> dict:
        return self._data.setdefault("process", {
            "pid": None, "last_tick": None, "held": False,
        })

    @property
    def github_story_items(self) -> dict:
        """Return {story_id: {github_issue, github_pr}} from implementation phase stories."""
        return {
            sid: {k: v for k, v in s.items() if k in ("github_issue", "github_pr")}
            for sid, s in self._impl.get("stories", {}).items()
            if sid.startswith("STORY-")
        }

    @property
    def approval_needed(self) -> bool:
        return self.is_approval_gate()

    def is_approval_gate(self) -> bool:
        return _is_approval_gate(self.phase, self.status)

    def is_executable(self) -> bool:
        return _is_executable(self.phase, self.status)

    def is_done(self) -> bool:
        return self.phase == Phase.DONE

    def label(self) -> str:
        if self.status == Status.BLOCKED:
            return f"BLOCKED — {self.blocked_reason or 'needs intervention'}"
        if self.phase == Phase.IMPLEMENTATION and self.current_story:
            action = {
                StoryStatus.PENDING:         f"{self.current_story} pending",
                StoryStatus.IN_PROGRESS:     f"Implementing {self.current_story}",
                StoryStatus.AWAITING_REVIEW: f"{self.current_story} PR open — awaiting approval",
                StoryStatus.FEEDBACK:        f"Incorporating feedback on {self.current_story}",
                StoryStatus.DONE:            f"{self.current_story} done",
            }.get(self.story_status or StoryStatus.IN_PROGRESS, "")
            return action
        return f"{PHASE_LABELS.get(self.phase, self.phase.value)}: {STATUS_LABELS.get(self.status, self.status.value)}"

    # ── mutations ────────────────────────────────────────────────────────────

    def submit_for_approval(self) -> None:
        """Mark current phase/story as ready for review."""
        self._push_history()
        if self.phase == Phase.IMPLEMENTATION and self.current_story:
            self._set_story_status(StoryStatus.AWAITING_REVIEW)
        else:
            self._data["status"] = Status.AWAITING_APPROVAL.value
            self._data["phases"][self.phase.value]["status"] = Status.AWAITING_APPROVAL.value
        self._data["retry_count"] = 0
        self.save()
        if self._notifier:
            self._notifier(self.phase, self._latest_artifact_path())

    def approve(self) -> None:
        """Advance past the current approval gate."""
        self._push_history()
        if self.phase == Phase.IMPLEMENTATION:
            self._approve_story()
        else:
            self._advance_phase()

    def _advance_phase(self) -> None:
        """Complete current phase and start the next one."""
        self._data["phases"][self.phase.value]["status"] = Status.DONE.value
        self._data["retry_count"] = 0
        idx = PHASE_ORDER.index(self.phase) if self.phase in PHASE_ORDER else -1
        if idx >= 0 and idx + 1 < len(PHASE_ORDER):
            next_phase = PHASE_ORDER[idx + 1]
            self._data["phase"] = next_phase.value
            self._data["status"] = Status.IN_PROGRESS.value
            self._data["phases"][next_phase.value]["status"] = Status.IN_PROGRESS.value
        else:
            self._data["phase"] = Phase.DONE.value
            self._data["status"] = Status.DONE.value
        self.save()

    def _approve_story(self) -> None:
        """Mark current story done. Caller must call start_next_story() or finish_implementation()."""
        sid = self.current_story
        if sid:
            story = self._impl.setdefault("stories", {}).setdefault(sid, {})
            story["status"] = StoryStatus.DONE.value
        self._impl["current_story"] = None
        self._data["retry_count"] = 0
        self.save()

    def start_story(self, story_id: str) -> None:
        """Begin work on a story in the implementation phase."""
        self._push_history()
        self._data["phase"] = Phase.IMPLEMENTATION.value
        self._data["status"] = Status.IN_PROGRESS.value
        self._impl["current_story"] = story_id
        story = self._impl.setdefault("stories", {}).setdefault(story_id, {})
        story["status"] = StoryStatus.IN_PROGRESS.value
        story.setdefault("current_task", None)
        story.setdefault("tasks", {})
        self._data["retry_count"] = 0
        self.save()

    def complete_story(self) -> Optional[str]:
        """Finish current story; return next pending story id or None if all done."""
        self._approve_story()
        all_stories = sorted(self._impl.get("stories", {}).keys())
        done = set(self.completed_stories)
        pending = [s for s in all_stories if s not in done and s.startswith("STORY-")]
        return pending[0] if pending else None

    def set_story_status(self, story_status: StoryStatus) -> None:
        sid = self.current_story
        if not sid:
            return
        self._set_story_status(story_status)
        self.save()

    def _set_story_status(self, story_status: StoryStatus) -> None:
        sid = self.current_story
        if not sid:
            return
        story = self._impl.setdefault("stories", {}).setdefault(sid, {})
        story["status"] = story_status.value
        if story_status == StoryStatus.IN_PROGRESS:
            self._data["status"] = Status.IN_PROGRESS.value
        elif story_status == StoryStatus.AWAITING_REVIEW:
            self._data["status"] = Status.AWAITING_APPROVAL.value

    def finish_implementation(self) -> None:
        """All stories complete — advance to testing phase."""
        self._push_history()
        self._data["phases"][Phase.IMPLEMENTATION.value]["status"] = Status.DONE.value
        self._data["phase"] = Phase.TESTING.value
        self._data["status"] = Status.IN_PROGRESS.value
        self._data["phases"][Phase.TESTING.value]["status"] = Status.IN_PROGRESS.value
        self._data["retry_count"] = 0
        self.save()

    def set_blocked(self, reason: str) -> None:
        self._push_history()
        self._data["blocked_reason"] = reason
        self._data["status"] = Status.BLOCKED.value
        self.save()

    def unblock(self) -> None:
        self._push_history()
        self._data["blocked_reason"] = None
        self._data["status"] = Status.IN_PROGRESS.value
        self.save()

    def update_task(self, story_id: str, task_id: str, status: str = "in_progress") -> None:
        story = self._impl.setdefault("stories", {}).setdefault(story_id, {
            "status": StoryStatus.IN_PROGRESS.value, "current_task": None, "tasks": {}
        })
        story.setdefault("tasks", {})[task_id] = status
        if status == "in_progress":
            story["current_task"] = task_id
        elif story.get("current_task") == task_id:
            story["current_task"] = None
        self.save()

    def increment_retry(self) -> None:
        self._data["retry_count"] = self.retry_count + 1
        self.save()

    def mark_artifact(self, key: str, path: str) -> None:
        self._data.setdefault("artifacts", {})[key] = path
        self.save()

    def set_story_item(self, story_id: str, github_issue: int,
                       github_pr: Optional[int] = None, phase: Optional[str] = None) -> None:
        """Store github issue/PR number inside the phase story entry."""
        if phase is None:
            # Detect phase: STORY-NNN → implementation, else use story_id as phase key
            phase = Phase.IMPLEMENTATION.value if story_id.startswith("STORY-") else story_id
        phase_entry = self._data["phases"].setdefault(phase, {"status": Status.PENDING.value, "stories": {}})
        story = phase_entry.setdefault("stories", {}).setdefault(story_id, {"status": Status.PENDING.value})
        story["github_issue"] = github_issue
        if github_pr is not None:
            story["github_pr"] = github_pr
        self.save()

    def set_work_item_links(self, item_id: str, phase: Optional[str] = None,
                            github_pr: Optional[int] = None,
                            commits: Optional[list[str]] = None) -> None:
        """Store PR and commit links for a phase or implementation story."""
        if phase is None:
            phase = Phase.IMPLEMENTATION.value if item_id.startswith("STORY-") else item_id
        story_id = item_id if item_id.startswith("STORY-") else phase
        phase_entry = self._data["phases"].setdefault(phase, {
            "status": Status.PENDING.value,
            "stories": {},
        })
        story = phase_entry.setdefault("stories", {}).setdefault(story_id, {
            "status": Status.PENDING.value,
        })
        if github_pr is not None:
            story["github_pr"] = github_pr
        if commits is not None:
            story["commits"] = commits
        self.save()

    def set_task_item(self, story_id: str, task_id: str, github_issue: int) -> None:
        """Store github issue number inside a task entry."""
        story = self._impl.setdefault("stories", {}).setdefault(story_id, {
            "status": StoryStatus.IN_PROGRESS.value, "current_task": None, "tasks": {}
        })
        task = story.setdefault("tasks", {}).setdefault(task_id, {})
        if isinstance(task, str):
            task = {"status": task}
        task["github_issue"] = github_issue
        story["tasks"][task_id] = task
        self.save()

    def set_process(self, pid: Optional[int] = None, last_tick: Optional[float] = None,
                    held: Optional[bool] = None) -> None:
        p = self._process
        if pid is not None:
            p["pid"] = pid
        if last_tick is not None:
            p["last_tick"] = last_tick
        if held is not None:
            p["held"] = held
        self.save()

    def _push_history(self) -> None:
        entry: dict = {
            "phase": self.phase.value,
            "status": self.status.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if self.current_story:
            entry["story"] = self.current_story
        self._data.setdefault("history", []).append(entry)

    def _latest_artifact_path(self) -> str:
        artifacts_dir = self.path.parent / "artifacts"
        if not artifacts_dir.exists():
            return ""
        files = sorted(artifacts_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        return str(files[0]) if files else ""


# ── helpers ───────────────────────────────────────────────────────────────────

def _default_phases() -> dict:
    phases: dict = {}
    for p in PHASE_ORDER:
        if p == Phase.IMPLEMENTATION:
            phases[p.value] = _default_impl_phase()
        else:
            phases[p.value] = {"status": Status.PENDING.value, "stories": {}}
    return phases


def _default_impl_phase() -> dict:
    return {
        "status": Status.PENDING.value,
        "current_story": None,
        "stories": {},
    }


def _migrate_github_items(phases: dict, story_items: dict, task_items: dict) -> None:
    """Move old flat github.story_items / task_items into phase stories."""
    for sid, val in story_items.items():
        num = val.get("number") if isinstance(val, dict) else val
        if not num:
            continue
        if sid.startswith("STORY-"):
            impl = phases.setdefault(Phase.IMPLEMENTATION.value, _default_impl_phase())
            story = impl.setdefault("stories", {}).setdefault(sid, {"status": Status.PENDING.value})
            story.setdefault("github_issue", num)
        else:
            # phase-named entry (e.g. "requirement", "design", "planning")
            phase_entry = phases.setdefault(sid, {"status": Status.PENDING.value, "stories": {}})
            story = phase_entry.setdefault("stories", {}).setdefault(sid, {"status": Status.PENDING.value})
            story.setdefault("github_issue", num)
    for tid, val in task_items.items():
        num = val.get("number") if isinstance(val, dict) else val
        if not num:
            continue
        impl = phases.setdefault(Phase.IMPLEMENTATION.value, _default_impl_phase())
        # Tasks are nested inside stories; without knowing the parent story, attach to first in-progress
        for sid, s in impl.get("stories", {}).items():
            if s.get("status") != StoryStatus.DONE.value:
                task = s.setdefault("tasks", {}).setdefault(tid, {})
                if isinstance(task, str):
                    s["tasks"][tid] = {"status": task, "github_issue": num}
                else:
                    task.setdefault("github_issue", num)
                break


def _backfill_phases(phases: dict, current_phase: Optional[Phase],
                     current_status: Status, data: dict) -> None:
    """Mark earlier phases as done when migrating from a flat state."""
    if current_phase is None or current_phase == Phase.DONE:
        for p in PHASE_ORDER:
            phases.setdefault(p.value, {})["status"] = Status.DONE.value
        return
    for p in PHASE_ORDER:
        if p == current_phase:
            phases.setdefault(p.value, {})["status"] = current_status.value
            break
        phases.setdefault(p.value, {})["status"] = Status.DONE.value

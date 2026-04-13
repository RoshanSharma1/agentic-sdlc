"""
State machine: phases → stories → done.

Flow:
  requirement → design → plan → [story_in_progress → story_awaiting_review] × N → done

Each story (STORY-NNN) is a self-contained unit: its own branch, PR, and approval gate.
The implementation phase is replaced by per-story cycles so large projects split cleanly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from sdlc_orchestrator.utils import sdlc_home


class State(str, Enum):
    REQUIREMENT_IN_PROGRESS        = "requirement_in_progress"
    REQUIREMENT_READY_FOR_APPROVAL = "requirement_ready_for_approval"
    DESIGN_IN_PROGRESS             = "design_in_progress"
    AWAITING_DESIGN_APPROVAL       = "awaiting_design_approval"
    TASK_PLAN_IN_PROGRESS          = "task_plan_in_progress"
    TASK_PLAN_READY                = "task_plan_ready"
    STORY_IN_PROGRESS              = "story_in_progress"
    STORY_AWAITING_REVIEW          = "story_awaiting_review"
    FEEDBACK_INCORPORATION         = "feedback_incorporation"
    BLOCKED                        = "blocked"
    DONE                           = "done"


# Valid transitions: state → allowed next states
TRANSITIONS: dict[State, list[State]] = {
    State.REQUIREMENT_IN_PROGRESS:         [State.REQUIREMENT_READY_FOR_APPROVAL],
    State.REQUIREMENT_READY_FOR_APPROVAL:  [State.DESIGN_IN_PROGRESS],
    State.DESIGN_IN_PROGRESS:             [State.AWAITING_DESIGN_APPROVAL],
    State.AWAITING_DESIGN_APPROVAL:        [State.TASK_PLAN_IN_PROGRESS],
    State.TASK_PLAN_IN_PROGRESS:           [State.TASK_PLAN_READY],
    State.TASK_PLAN_READY:                 [State.STORY_IN_PROGRESS],
    State.STORY_IN_PROGRESS:              [State.STORY_AWAITING_REVIEW, State.BLOCKED],
    State.STORY_AWAITING_REVIEW:          [State.STORY_IN_PROGRESS, State.FEEDBACK_INCORPORATION, State.DONE],
    State.FEEDBACK_INCORPORATION:          [State.REQUIREMENT_IN_PROGRESS, State.DESIGN_IN_PROGRESS,
                                            State.TASK_PLAN_IN_PROGRESS, State.STORY_IN_PROGRESS],
    State.BLOCKED:                         [State.REQUIREMENT_IN_PROGRESS, State.DESIGN_IN_PROGRESS,
                                            State.TASK_PLAN_IN_PROGRESS, State.STORY_IN_PROGRESS],
    State.DONE:                            [],
}

# States where execution is paused waiting for a human action
APPROVAL_STATES: set[State] = {
    State.REQUIREMENT_READY_FOR_APPROVAL,
    State.AWAITING_DESIGN_APPROVAL,
    State.TASK_PLAN_READY,
    State.STORY_AWAITING_REVIEW,
    State.BLOCKED,
}

# States where the agent should run
EXECUTABLE_STATES: set[State] = {
    State.REQUIREMENT_IN_PROGRESS,
    State.DESIGN_IN_PROGRESS,
    State.TASK_PLAN_IN_PROGRESS,
    State.STORY_IN_PROGRESS,
    State.FEEDBACK_INCORPORATION,
}

# Human-readable labels for display
STATE_LABELS: dict[State, str] = {
    State.REQUIREMENT_IN_PROGRESS:         "Drafting requirements",
    State.REQUIREMENT_READY_FOR_APPROVAL:  "Requirements PR open — awaiting your approval",
    State.DESIGN_IN_PROGRESS:             "Designing system architecture",
    State.AWAITING_DESIGN_APPROVAL:        "Design ready — awaiting your approval",
    State.TASK_PLAN_IN_PROGRESS:           "Breaking design into stories and tasks",
    State.TASK_PLAN_READY:                 "Task plan ready — awaiting your approval",
    State.STORY_IN_PROGRESS:              "Implementing story",
    State.STORY_AWAITING_REVIEW:          "Story PR open — awaiting your approval",
    State.FEEDBACK_INCORPORATION:          "Incorporating feedback",
    State.BLOCKED:                         "BLOCKED — needs human intervention",
    State.DONE:                            "Complete",
}


class WorkflowState:
    """Loads, mutates, and persists workflow/state.json."""

    def __init__(self, project_dir: Path,
                 notifier: Optional[Callable[[State, str], None]] = None):
        """
        Args:
            project_dir: root of the project
            notifier: optional callable(new_state, artifact_path) invoked
                      automatically when a transition lands on an approval gate.
                      Keeping this as a callable injection keeps state_machine.py
                      free of integration imports.
        """
        self.project_dir = project_dir.resolve()
        self._notifier = notifier
        _home = sdlc_home(project_dir)
        self.path = _home / "workflow" / "state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return self._defaults()

    def save(self) -> None:
        self._data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps(self._data, indent=2))

    @staticmethod
    def _defaults() -> dict:
        return {
            "state": State.REQUIREMENT_IN_PROGRESS.value,
            "retry_count": 0,
            "approval_needed": False,
            "blocked_reason": None,
            "current_branch": "main",
            # Story tracking (implementation phase)
            "current_story": None,           # STORY-NNN currently being worked
            "completed_stories": [],         # [STORY-001, STORY-002, ...] approved stories
            # GitHub project board (Projects v2)
            "github_project": None,          # {number, node_id, status_field_id, status_options}
            "github_epic_issue": None,       # epic issue number
            "github_phase_items": {},        # {phase: {issue, item_id}} — board items per phase
            "github_story_items": {},        # {STORY-001: {issue, item_id}} — per user story
            "github_task_items": {},         # {TASK-001: {issue, item_id}} — per task
            "artifacts": {
                "requirement_questions": None,
                "requirements": None,
                "design": None,
                "plan": None,
                "test_report": None,
                "review_summary": None,
            },
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "history": [],
        }

    # ── accessors ────────────────────────────────────────────────────────────

    @property
    def state(self) -> State:
        return State(self._data["state"])

    @property
    def retry_count(self) -> int:
        return self._data.get("retry_count", 0)

    @property
    def approval_needed(self) -> bool:
        return self._data.get("approval_needed", False)

    @property
    def blocked_reason(self) -> Optional[str]:
        return self._data.get("blocked_reason")

    @property
    def artifacts(self) -> dict:
        return self._data.get("artifacts", {})

    @property
    def current_story(self) -> Optional[str]:
        return self._data.get("current_story")

    @property
    def completed_stories(self) -> list:
        return self._data.get("completed_stories", [])

    @property
    def github_project(self) -> Optional[dict]:
        return self._data.get("github_project")

    @property
    def github_epic_issue(self) -> Optional[int]:
        return self._data.get("github_epic_issue")

    @property
    def github_phase_items(self) -> dict:
        return self._data.get("github_phase_items", {})

    @property
    def github_story_items(self) -> dict:
        return self._data.get("github_story_items", {})

    @property
    def github_task_items(self) -> dict:
        return self._data.get("github_task_items", {})

    @property
    def github_project_id(self) -> Optional[str]:
        return self._data.get("github_project_id")

    def is_approval_gate(self) -> bool:
        return self.state in APPROVAL_STATES

    def is_executable(self) -> bool:
        return self.state in EXECUTABLE_STATES

    def is_done(self) -> bool:
        return self.state == State.DONE

    # ── mutations ────────────────────────────────────────────────────────────

    def transition(self, new_state: State) -> None:
        allowed = TRANSITIONS.get(self.state, [])
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {self.state} → {new_state}. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        self._push_history(self.state)
        self._data["state"] = new_state.value
        self._data["approval_needed"] = new_state in APPROVAL_STATES
        if new_state != State.FEEDBACK_INCORPORATION:
            self._data["retry_count"] = 0
        self.save()
        if new_state in APPROVAL_STATES and self._notifier:
            artifact_path = self._latest_artifact_path()
            self._notifier(new_state, artifact_path)

    def increment_retry(self) -> None:
        self._data["retry_count"] = self.retry_count + 1
        self.save()

    def set_blocked(self, reason: str) -> None:
        self._data["blocked_reason"] = reason
        self.transition(State.BLOCKED)

    def mark_artifact(self, key: str, path: str) -> None:
        self._data.setdefault("artifacts", {})[key] = path
        self.save()

    def set_current_story(self, story_id: str) -> None:
        """Set the story currently being worked on."""
        self._data["current_story"] = story_id
        self.save()

    def complete_current_story(self) -> None:
        """Mark current story as complete and clear it."""
        story = self._data.get("current_story")
        if story and story not in self._data.setdefault("completed_stories", []):
            self._data["completed_stories"].append(story)
        self._data["current_story"] = None
        self.save()

    def set_github(self, epic_issue: Optional[int] = None,
                   project_id: Optional[str] = None) -> None:
        if epic_issue is not None:
            self._data["github_epic_issue"] = epic_issue
        if project_id is not None:
            self._data["github_project_id"] = project_id
        self.save()

    def set_github_project(self, project_info: dict) -> None:
        self._data["github_project"] = project_info
        self.save()

    def set_phase_item(self, phase: str, issue: int, item_id: str) -> None:
        self._data.setdefault("github_phase_items", {})[phase] = {
            "issue": issue, "item_id": item_id,
        }
        self.save()

    def set_story_items(self, story_items: dict) -> None:
        """Merge story_items ({STORY-001: {issue, item_id}}) into state."""
        self._data.setdefault("github_story_items", {}).update(story_items)
        self.save()

    def set_task_items(self, task_items: dict) -> None:
        """Merge task_items ({TASK-001: {issue, item_id}}) into state."""
        self._data.setdefault("github_task_items", {}).update(task_items)
        self.save()

    def _push_history(self, state: State) -> None:
        self._data.setdefault("history", []).append({
            "state": state.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _latest_artifact_path(self) -> str:
        """Return the path of the most recently modified artifact file, or ''."""
        artifacts_dir = self.path.parent / "artifacts"
        if not artifacts_dir.exists():
            return ""
        files = sorted(artifacts_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        return str(files[0]) if files else ""

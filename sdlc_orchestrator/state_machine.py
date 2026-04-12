"""
State machine: 12 states, transitions, approval gates, and executable states.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class State(str, Enum):
    DRAFT_REQUIREMENT            = "draft_requirement"
    AWAITING_REQUIREMENT_ANSWER  = "awaiting_requirement_answer"
    REQUIREMENT_IN_PROGRESS      = "requirement_in_progress"
    REQUIREMENT_READY_FOR_APPROVAL = "requirement_ready_for_approval"
    DESIGN_IN_PROGRESS           = "design_in_progress"
    AWAITING_DESIGN_APPROVAL     = "awaiting_design_approval"
    TASK_PLAN_IN_PROGRESS        = "task_plan_in_progress"
    TASK_PLAN_READY              = "task_plan_ready"
    IMPLEMENTATION_IN_PROGRESS   = "implementation_in_progress"
    TEST_FAILURE_LOOP            = "test_failure_loop"
    AWAITING_REVIEW              = "awaiting_review"
    FEEDBACK_INCORPORATION       = "feedback_incorporation"
    BLOCKED                      = "blocked"
    DONE                         = "done"


# Valid transitions: state → allowed next states
TRANSITIONS: dict[State, list[State]] = {
    State.DRAFT_REQUIREMENT:               [State.AWAITING_REQUIREMENT_ANSWER],
    State.AWAITING_REQUIREMENT_ANSWER:     [State.REQUIREMENT_IN_PROGRESS],
    State.REQUIREMENT_IN_PROGRESS:         [State.REQUIREMENT_READY_FOR_APPROVAL],
    State.REQUIREMENT_READY_FOR_APPROVAL:  [State.DESIGN_IN_PROGRESS],
    State.DESIGN_IN_PROGRESS:             [State.AWAITING_DESIGN_APPROVAL],
    State.AWAITING_DESIGN_APPROVAL:        [State.TASK_PLAN_IN_PROGRESS],
    State.TASK_PLAN_IN_PROGRESS:           [State.TASK_PLAN_READY],
    State.TASK_PLAN_READY:                 [State.IMPLEMENTATION_IN_PROGRESS],
    State.IMPLEMENTATION_IN_PROGRESS:      [State.TEST_FAILURE_LOOP, State.AWAITING_REVIEW],
    State.TEST_FAILURE_LOOP:               [State.AWAITING_REVIEW, State.BLOCKED],
    State.AWAITING_REVIEW:                 [State.FEEDBACK_INCORPORATION, State.DONE],
    State.FEEDBACK_INCORPORATION:          [State.DESIGN_IN_PROGRESS, State.TASK_PLAN_IN_PROGRESS,
                                            State.IMPLEMENTATION_IN_PROGRESS],
    State.BLOCKED:                         [State.DRAFT_REQUIREMENT, State.DESIGN_IN_PROGRESS,
                                            State.TASK_PLAN_IN_PROGRESS, State.IMPLEMENTATION_IN_PROGRESS],
    State.DONE:                            [],
}

# States where execution is paused waiting for a human action
APPROVAL_STATES: set[State] = {
    State.AWAITING_REQUIREMENT_ANSWER,
    State.REQUIREMENT_READY_FOR_APPROVAL,
    State.AWAITING_DESIGN_APPROVAL,
    State.TASK_PLAN_READY,
    State.AWAITING_REVIEW,
    State.BLOCKED,
}

# States where the agent should run
EXECUTABLE_STATES: set[State] = {
    State.DRAFT_REQUIREMENT,
    State.REQUIREMENT_IN_PROGRESS,
    State.DESIGN_IN_PROGRESS,
    State.TASK_PLAN_IN_PROGRESS,
    State.IMPLEMENTATION_IN_PROGRESS,
    State.TEST_FAILURE_LOOP,
    State.FEEDBACK_INCORPORATION,
}

# Human-readable labels for display
STATE_LABELS: dict[State, str] = {
    State.DRAFT_REQUIREMENT:               "Drafting requirement questions",
    State.AWAITING_REQUIREMENT_ANSWER:     "Awaiting your answers to clarifying questions",
    State.REQUIREMENT_IN_PROGRESS:         "Building structured requirements",
    State.REQUIREMENT_READY_FOR_APPROVAL:  "Requirements ready — awaiting your approval",
    State.DESIGN_IN_PROGRESS:             "Designing system architecture",
    State.AWAITING_DESIGN_APPROVAL:        "Design ready — awaiting your approval",
    State.TASK_PLAN_IN_PROGRESS:           "Breaking design into tasks",
    State.TASK_PLAN_READY:                 "Task plan ready — awaiting your approval",
    State.IMPLEMENTATION_IN_PROGRESS:      "Implementing features",
    State.TEST_FAILURE_LOOP:               "Fixing failing tests",
    State.AWAITING_REVIEW:                 "Ready for review — awaiting your approval",
    State.FEEDBACK_INCORPORATION:          "Incorporating feedback",
    State.BLOCKED:                         "BLOCKED — needs human intervention",
    State.DONE:                            "Complete",
}


class WorkflowState:
    """Loads, mutates, and persists workflow/state.json."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.path = project_dir / "workflow" / "state.json"
        self._data: dict = self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            # Migrate old schema (phase/phase_state → state)
            if "state" not in data and "phase" in data:
                data = self._defaults()
            return data
        return self._defaults()

    def save(self) -> None:
        self._data["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps(self._data, indent=2))

    @staticmethod
    def _defaults() -> dict:
        return {
            "state": State.DRAFT_REQUIREMENT.value,
            "retry_count": 0,
            "approval_needed": False,
            "blocked_reason": None,
            "current_branch": "main",
            "github_epic_issue": None,
            "github_project_id": None,
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
    def github_epic_issue(self) -> Optional[int]:
        return self._data.get("github_epic_issue")

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
        if new_state not in (State.TEST_FAILURE_LOOP, State.FEEDBACK_INCORPORATION):
            self._data["retry_count"] = 0
        self.save()

    def increment_retry(self) -> None:
        self._data["retry_count"] = self.retry_count + 1
        self.save()

    def set_blocked(self, reason: str) -> None:
        self._data["blocked_reason"] = reason
        self.transition(State.BLOCKED)

    def mark_artifact(self, key: str, path: str) -> None:
        self._data.setdefault("artifacts", {})[key] = path
        self.save()

    def set_github(self, epic_issue: Optional[int] = None,
                   project_id: Optional[str] = None) -> None:
        if epic_issue is not None:
            self._data["github_epic_issue"] = epic_issue
        if project_id is not None:
            self._data["github_project_id"] = project_id
        self.save()

    def _push_history(self, state: State) -> None:
        self._data.setdefault("history", []).append({
            "state": state.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

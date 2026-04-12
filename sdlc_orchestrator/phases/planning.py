from __future__ import annotations
from pathlib import Path
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler


class PlanningHandler(PhaseHandler):
    phase_name = "planning"
    entry_state = State.TASK_PLAN_IN_PROGRESS
    success_state = State.TASK_PLAN_READY

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context("planning", extra_artifacts=["requirements", "design"])
        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context)

        return f"""{context}

## Your task — Task Planning

You are acting as a Project Manager.

Based on the requirements and design documents, produce:

1. **workflow/artifacts/plan.md** containing a flat task list:

   Each task must have:
   - ID: TASK-NNN
   - Title (one line)
   - Size: S | M | L
   - Dependencies: comma-separated TASK IDs (or "none")
   - Description: 2–4 sentences
   - Test expectations: what tests will verify this task

   Order tasks by implementation dependency (no task before its dependencies).

2. **workflow/artifacts/github_tasks.md** — one GitHub child issue body per task,
   ready to be created as children of the Epic.

When done, output exactly: PHASE_COMPLETE: planning
"""

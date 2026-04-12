from __future__ import annotations
from pathlib import Path
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler


class ImplementationHandler(PhaseHandler):
    phase_name = "implementation"
    entry_state = State.IMPLEMENTATION_IN_PROGRESS
    success_state = State.TEST_FAILURE_LOOP  # always goes to validate next

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context(
            "implementation",
            extra_artifacts=["requirements", "design", "plan"],
        )
        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context)

        return f"""{context}

## Your task — Implementation

You are acting as a Senior Developer.

Work through the tasks in workflow/artifacts/plan.md **in dependency order**:

For each task:
1. Implement the feature following the design document exactly
2. Write unit tests before or alongside the implementation (TDD preferred)
3. Update plan.md: mark the task `[x] done` when complete
4. Commit with message: `feat(TASK-NNN): <title>`

Rules (from CLAUDE.md apply in full):
- Follow modular architecture — each module has a single responsibility
- No secrets or credentials in code — use environment variables
- Keep functions small and focused

When ALL tasks in plan.md are marked done, output exactly: PHASE_COMPLETE: implementation
"""

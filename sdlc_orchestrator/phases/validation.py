from __future__ import annotations
from pathlib import Path
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler

MAX_TEST_RETRIES = 3


class ValidationHandler(PhaseHandler):
    phase_name = "validation"
    entry_state = State.TEST_FAILURE_LOOP
    success_state = State.AWAITING_REVIEW

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context(
            "validation",
            extra_artifacts=["requirements", "plan"],
        )
        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context)

        spec = memory.spec()
        tech = spec.get("tech_stack", "")

        return f"""{context}

## Your task — Validation

You are acting as a QA Engineer.

1. Run all tests and linting for this project ({tech})
2. Fix every failing test or lint error — do not skip, comment out, or weaken assertions
3. Verify all acceptance criteria from workflow/artifacts/requirements.md are met
4. Write workflow/artifacts/test_report.md:
   - Test suite summary (pass/fail counts)
   - Coverage percentage
   - Each requirement's acceptance criteria: ✓ met / ✗ not met
   - Any remaining issues or known limitations

If all tests pass and all acceptance criteria are met:
  → output exactly: PHASE_COMPLETE: validation

If there are failures you cannot fix after 3 attempts:
  → document them in test_report.md under "Blockers"
  → output exactly: PHASE_BLOCKED: <one-line reason>
"""

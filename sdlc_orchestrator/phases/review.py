from __future__ import annotations
from pathlib import Path
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler


class ReviewHandler(PhaseHandler):
    phase_name = "review"
    entry_state = State.AWAITING_REVIEW
    # AWAITING_REVIEW is a human gate — this handler runs when human approves
    # and the state machine moves forward. This handler just produces the
    # final review_summary.md before done.
    success_state = State.DONE

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context(
            "review",
            extra_artifacts=["requirements", "design", "plan", "test_report"],
        )
        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context)

        return f"""{context}

## Your task — Final Review

You are acting as a Tech Lead doing the final review and sign-off.

1. Verify every functional requirement in requirements.md has a passing test
2. Verify the implementation follows design.md — flag any deviations
3. Check CLAUDE.md rules are followed throughout the codebase
4. Review for security issues, performance red flags, and tech debt
5. Write workflow/artifacts/review_summary.md:
   - Requirements coverage matrix (req ID → status)
   - Design compliance notes
   - Code quality score (1–10) with rationale
   - Security findings
   - Outstanding tech debt
   - Final sign-off statement (or escalation if blocking issues found)

If sign-off is clean: output exactly: PHASE_COMPLETE: review
If blocking issues: output exactly: PHASE_BLOCKED: <one-line reason>
"""

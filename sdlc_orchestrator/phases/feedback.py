from __future__ import annotations
from pathlib import Path
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler


class FeedbackHandler(PhaseHandler):
    """
    Reads all queued feedback (from PR comments, issues, or manual files),
    maps each item to the appropriate phase artifact, updates those artifacts,
    then re-enters the relevant phase.

    The target re-entry state is determined by the feedback itself:
      - design changes  → DESIGN_IN_PROGRESS
      - task/scope      → TASK_PLAN_IN_PROGRESS
      - code/bugs       → IMPLEMENTATION_IN_PROGRESS
    """

    phase_name = "feedback"
    entry_state = State.FEEDBACK_INCORPORATION
    success_state = State.IMPLEMENTATION_IN_PROGRESS  # default; overridden by output

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context(
            "feedback",
            extra_artifacts=["requirements", "design", "plan"],
        )
        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context)

        # Collect all feedback files
        feedback_dir = project_dir / "feedback"
        all_feedback: list[str] = []
        if feedback_dir.exists():
            for fb_file in sorted(feedback_dir.glob("*.md")):
                content = fb_file.read_text().strip()
                if content:
                    all_feedback.append(f"### From {fb_file.stem}\n{content}")

        feedback_block = "\n\n".join(all_feedback) or "(no feedback files found)"

        return f"""{context}

## Collected Feedback
{feedback_block}

## Your task — Feedback Incorporation

You are acting as a Senior Developer incorporating review feedback.

1. Read all feedback above carefully
2. Categorise each item:
   - `[design]` — requires changes to workflow/artifacts/design.md
   - `[plan]`   — requires changes to workflow/artifacts/plan.md
   - `[code]`   — requires direct code changes
   - `[docs]`   — requires documentation updates only

3. Apply every change in the appropriate artifact or code file
4. Commit each logical change separately: `fix(feedback): <description>`
5. Clear applied feedback by moving feedback files to feedback/applied/

6. At the end, output ONE of:
   - `PHASE_COMPLETE: feedback:design`       — if design was changed (re-enters design)
   - `PHASE_COMPLETE: feedback:plan`         — if only plan/tasks changed
   - `PHASE_COMPLETE: feedback:code`         — if only code/docs changed (re-enters impl)
"""

from __future__ import annotations

from pathlib import Path

from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler


class RequirementDiscoveryHandler(PhaseHandler):
    """
    DRAFT_REQUIREMENT state:
    Claude asks clarifying questions → writes requirement_questions.md
    → transitions to AWAITING_REQUIREMENT_ANSWER (human gate)
    """

    phase_name = "requirement-discovery"
    entry_state = State.DRAFT_REQUIREMENT
    success_state = State.AWAITING_REQUIREMENT_ANSWER

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context("requirement")
        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context)

        return f"""{context}

## Your task — Requirement Discovery

You are acting as a Business Analyst conducting a requirements interview.

1. Read spec.yaml carefully. Identify every ambiguity, assumption, and missing detail.
2. Produce a list of 5–10 clarifying questions that, when answered, will remove all ambiguity.
   Format each question with an empty "Answer:" field so the human can fill it in.
3. Write the questions to: workflow/artifacts/requirement_questions.md

Format:
```
# Requirement Clarifying Questions

## Q1: <question title>
<detailed question>
**Answer:** (fill in below)

## Q2: ...
```

When the file is written, output exactly: PHASE_COMPLETE: requirement-discovery
"""

    def post_run(self, output: str, memory: MemoryManager, project_dir: Path) -> None:
        questions_path = project_dir / "workflow" / "artifacts" / "requirement_questions.md"
        if questions_path.exists():
            memory._data = {}  # no-op, artifact tracked by WorkflowState


class RequirementBuildHandler(PhaseHandler):
    """
    REQUIREMENT_IN_PROGRESS state:
    Claude reads questions + answers → writes requirements.md
    → transitions to REQUIREMENT_READY_FOR_APPROVAL
    """

    phase_name = "requirement"
    entry_state = State.REQUIREMENT_IN_PROGRESS
    success_state = State.REQUIREMENT_READY_FOR_APPROVAL

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context("requirement")
        questions_path = project_dir / "workflow" / "artifacts" / "requirement_questions.md"
        qa_block = ""
        if questions_path.exists():
            qa_block = f"\n## Answered Clarifying Questions\n{questions_path.read_text()}\n"

        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context, {"QA": qa_block})

        return f"""{context}
{qa_block}
## Your task — Build Structured Requirements

Based on the original spec and the answered clarifying questions above:

1. Write a comprehensive requirements document to workflow/artifacts/requirements.md
2. Include:
   - Project overview and goals
   - Scope (what is included) and non-goals (what is excluded)
   - Functional requirements (numbered, with acceptance criteria)
   - Non-functional requirements (performance, security, reliability)
   - Constraints and assumptions
   - Risks and mitigations
   - Success metrics / definition of done
3. Create a GitHub Epic issue summarising the project (title + body only, do not submit)
   and write it to workflow/artifacts/github_epic.md

When done, output exactly: PHASE_COMPLETE: requirement
"""

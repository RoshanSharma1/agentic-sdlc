from __future__ import annotations
from pathlib import Path
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler


class DesignHandler(PhaseHandler):
    phase_name = "design"
    entry_state = State.DESIGN_IN_PROGRESS
    success_state = State.AWAITING_DESIGN_APPROVAL

    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str:
        context = memory.build_context("design", extra_artifacts=["requirements"])
        template = self._load_skill_template(project_dir)
        if template:
            return self._inject_context(template, context)

        return f"""{context}

## Your task — System Design

You are acting as a Software Architect.

Based on the requirements document, produce:

1. **workflow/artifacts/design.md** containing:
   - System architecture overview (text-based component diagram)
   - Component breakdown: responsibilities, interfaces, dependencies
   - Data model / database schema
   - API contracts (endpoints, request/response shapes)
   - Technology choices with rationale
   - Security considerations
   - Scalability and performance approach
   - Known risks and tradeoffs

2. **workflow/artifacts/github_design_issue.md** — a GitHub child issue body
   summarising the design decisions for traceability.

Follow every rule in CLAUDE.md. When done, output exactly: PHASE_COMPLETE: design
"""

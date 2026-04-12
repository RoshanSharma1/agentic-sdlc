"""Base PhaseHandler — every phase subclasses this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import State


class PhaseHandler(ABC):
    # ── required ─────────────────────────────────────────────────────────────

    @property
    @abstractmethod
    def phase_name(self) -> str: ...

    @property
    @abstractmethod
    def entry_state(self) -> State:
        """The state this handler activates on."""
        ...

    @property
    @abstractmethod
    def success_state(self) -> State:
        """The state to transition to on clean completion."""
        ...

    @abstractmethod
    def build_prompt(self, memory: MemoryManager, project_dir: Path) -> str: ...

    # ── optional overrides ────────────────────────────────────────────────────

    @property
    def skill_file(self) -> str:
        """Name of the .claude/commands/ skill file to use, if any."""
        return f"sdlc-{self.phase_name}"

    def post_run(self, output: str, memory: MemoryManager, project_dir: Path) -> None:
        """Called after a successful executor run. Override to extract artifacts."""
        pass

    def on_blocked(self, error: str, memory: MemoryManager) -> str:
        """Returns a human-readable blocked reason."""
        return f"Phase '{self.phase_name}' failed: {error}"

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_skill_template(self, project_dir: Path) -> str:
        """Load skill template from .claude/commands/ or package skills/."""
        local = project_dir / ".claude" / "commands" / f"{self.skill_file}.md"
        if local.exists():
            return local.read_text()

        # Fall back to bundled skill template
        from importlib.resources import files
        try:
            skill_text = (
                files("sdlc_orchestrator.skills")
                .joinpath(f"{self.skill_file}.md")
                .read_text(encoding="utf-8")
            )
            return skill_text
        except (FileNotFoundError, TypeError):
            return ""

    def _inject_context(self, template: str, context: str,
                        extra: dict[str, str] | None = None) -> str:
        """Replace {{PLACEHOLDERS}} in a skill template."""
        prompt = template.replace("{{MEMORY}}", context)
        for key, value in (extra or {}).items():
            prompt = prompt.replace(f"{{{{{key}}}}}", value)
        return prompt

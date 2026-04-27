"""
3-layer memory manager.

  Layer 1 — global.md   : coding values, DoD, review rules (machine-wide)
  Layer 2 — project.md  : stack, architecture, domain language, constraints
  Layer 3 — state.json  : execution memory (phase, state, tasks, decisions)

All state files live under sdlc_home(project_dir), which resolves to
project/.sdlc/ for the current worktree.

CLAUDE.md lives in the project root (not .sdlc/) so Claude Code finds it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import yaml

from sdlc_orchestrator.utils import sdlc_home


GLOBAL_MEMORY_PATH = Path.home() / ".sdlc" / "global.md"

# Per-executor config: (context_file_name, skills_dir, settings_dir)
EXECUTOR_CONFIG: dict[str, tuple[str, Path, Path]] = {
    "claude-code": ("CLAUDE.md",  Path.home() / ".claude" / "commands", Path(".claude")),
    "codex":       ("AGENTS.md",  Path.home() / ".codex"  / "commands", Path(".codex")),
    "kiro":        ("AGENT.md",   Path.home() / ".kiro"   / "skills",   Path(".kiro")),
    "cline":       ("AGENT.md",   Path.home() / ".cline"  / "commands", Path(".cline")),
}
DEFAULT_EXECUTOR = "claude-code"

# CLI command to trigger a headless phase skill per executor.
# {skill} is replaced with the resolved skill name (e.g. "sdlc-requirement").
EXECUTOR_CLI: dict[str, list[str]] = {
    "claude-code": ["claude", "-p", "--dangerously-skip-permissions", "/{skill}"],
    "codex":       ["codex", "exec", "--full-auto", "{skill}"],
    "kiro":        ["kiro-cli", "chat", "--agent", "{skill}", "--no-interactive", "start"],
    "cline":       [],  # Cline has no headless CLI — must be triggered manually
}


def executor_config(executor: str) -> tuple[str, Path, Path]:
    return EXECUTOR_CONFIG.get(executor, EXECUTOR_CONFIG[DEFAULT_EXECUTOR])


class MemoryManager:
    def __init__(self, project_dir: Path):
        self.project_dir = project_dir.resolve()
        self._sdlc = sdlc_home(project_dir)
        self.memory_dir = self._sdlc / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    # ── layer paths ──────────────────────────────────────────────────────────

    @property
    def global_path(self) -> Path:
        return GLOBAL_MEMORY_PATH

    @property
    def project_path(self) -> Path:
        return self.memory_dir / "project.md"

    @property
    def context_file_path(self) -> Path:
        """Returns the agent context file path based on executor in spec.yaml."""
        spec = self.spec()
        executor = spec.get("executor", DEFAULT_EXECUTOR)
        filename, _, _ = executor_config(executor)
        return self.project_dir / filename

    @property
    def claude_md_path(self) -> Path:
        # Backwards-compat alias
        return self.context_file_path

    @property
    def spec_path(self) -> Path:
        return self._sdlc / "spec.yaml"

    # ── readers ──────────────────────────────────────────────────────────────

    def global_memory(self) -> str:
        return self.global_path.read_text() if self.global_path.exists() else ""

    def project_memory(self) -> str:
        return self.project_path.read_text() if self.project_path.exists() else ""

    def spec(self) -> dict:
        if self.spec_path.exists():
            return yaml.safe_load(self.spec_path.read_text()) or {}
        try:
            from sdlc_orchestrator.backend import get_runtime
            from sdlc_orchestrator.utils import project_slug
            record = get_runtime().store.get_project(project_slug(self.project_dir))
            if record and record.spec:
                return record.spec
        except Exception:
            pass
        return {}

    def feedback(self, phase: str) -> str:
        fb = self._sdlc / "feedback" / f"{phase}.md"
        if fb.exists():
            content = fb.read_text().strip()
            return f"\n## Feedback from previous iteration\n{content}\n" if content else ""
        return ""

    def artifact(self, name: str) -> str:
        path = self._sdlc / "workflow" / "artifacts" / f"{name}.md"
        return path.read_text() if path.exists() else ""

    def execution_log(self, phase: str) -> str:
        log = self._sdlc / "workflow" / "logs" / f"{phase}.log"
        if not log.exists():
            return ""
        text = log.read_text()
        return text[-4000:] if len(text) > 4000 else text

    # ── writers ──────────────────────────────────────────────────────────────

    ALL_PHASES = ["requirement", "design", "planning", "implementation", "testing", "documentation"]

    def write_spec(self, spec: dict) -> None:
        self._sdlc.mkdir(parents=True, exist_ok=True)
        if "phase_approvals" not in spec:
            spec["phase_approvals"] = {p: True for p in self.ALL_PHASES}
        if "agent_fallback" not in spec:
            spec["agent_fallback"] = True  # Enable by default
        self.spec_path.write_text(yaml.dump(spec, default_flow_style=False))
        try:
            from sdlc_orchestrator.backend import sync_project_from_disk
            sync_project_from_disk(self.project_dir)
        except Exception:
            pass

    def set_phase_approvals(self, value: bool) -> None:
        spec = self.spec()
        spec["phase_approvals"] = {p: value for p in self.ALL_PHASES}
        self.spec_path.write_text(yaml.dump(spec, default_flow_style=False, sort_keys=False))

    def write_project_memory(self, content: str) -> None:
        self.project_path.write_text(content)
        self.regenerate_claude_md()

    def append_feedback(self, phase: str, text: str) -> None:
        fb_dir = self._sdlc / "feedback"
        fb_dir.mkdir(exist_ok=True)
        fb_file = fb_dir / f"{phase}.md"
        with fb_file.open("a") as f:
            from datetime import datetime, timezone
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            f.write(f"\n### {ts}\n{text}\n")

    def write_artifact(self, name: str, content: str) -> Path:
        artifacts_dir = self._sdlc / "workflow" / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = artifacts_dir / f"{name}.md"
        path.write_text(content)
        return path

    def regenerate_claude_md(self) -> None:
        """Merge global.md + project.md → agent context file in project root."""
        parts: list[str] = []

        global_mem = self.global_memory()
        if global_mem:
            parts.append("# Global Rules\n\n" + global_mem.strip())

        project_mem = self.project_memory()
        if project_mem:
            parts.append("# Project Context\n\n" + project_mem.strip())

        spec = self.spec()
        if spec:
            parts.append(
                "# Project Spec\n\n```yaml\n"
                + yaml.dump(spec, default_flow_style=False)
                + "```"
            )

        merged = "\n\n---\n\n".join(parts)
        self.context_file_path.write_text(
            "<!-- AUTO-GENERATED by sdlc — edit .sdlc/memory/project.md, not this file -->\n\n"
            + merged + "\n"
        )

    # ── context builder ───────────────────────────────────────────────────────

    def build_context(self, phase: str, extra_artifacts: Optional[list[str]] = None) -> str:
        spec = self.spec()
        lines: list[str] = [
            f"## Project: {spec.get('project_name', 'Unknown')}",
            f"Tech stack: {spec.get('tech_stack', 'Unknown')}",
            "",
            "## Global Rules",
            self.global_memory() or "(none)",
            "",
            "## Project Context",
            self.project_memory() or "(none)",
        ]

        for artifact_name in (extra_artifacts or []):
            content = self.artifact(artifact_name)
            if content:
                lines += ["", f"## {artifact_name.replace('_', ' ').title()}", content]

        feedback = self.feedback(phase)
        if feedback:
            lines += ["", feedback]

        prior_log = self.execution_log(phase)
        if prior_log:
            lines += [
                "",
                "## Prior execution log (resume context — do not repeat completed work)",
                prior_log,
            ]

        return "\n".join(lines)

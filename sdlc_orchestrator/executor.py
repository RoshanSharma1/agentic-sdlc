"""
Pluggable executor abstraction.

  v1: ClaudeCodeExecutor  — calls `claude -p` CLI
  v2: CodexExecutor       — stub (AGENTS.md mapping)

The orchestrator always calls Executor.run(prompt, cwd) and gets back
an ExecutionResult regardless of the backend.
"""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class ExecutionResult:
    success: bool
    output: str
    phase_complete: bool        # True if PHASE_COMPLETE marker found in output
    error: Optional[str] = None


class Executor(ABC):
    @abstractmethod
    def run(self, prompt: str, cwd: Path) -> ExecutionResult: ...

    @abstractmethod
    def name(self) -> str: ...


# ── Claude Code executor ─────────────────────────────────────────────────────

class ClaudeCodeExecutor(Executor):
    """Runs `echo prompt | claude -p --dangerously-skip-permissions` in cwd."""

    COMPLETE_MARKER = "PHASE_COMPLETE"

    def name(self) -> str:
        return "claude-code"

    def run(self, prompt: str, cwd: Path) -> ExecutionResult:
        try:
            result = subprocess.run(
                ["claude", "-p", "--dangerously-skip-permissions"],
                input=prompt,
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=900,  # 15 min ceiling per phase
            )
            output = result.stdout + result.stderr
            success = result.returncode == 0
            phase_complete = self.COMPLETE_MARKER in output
            error = result.stderr if not success else None
            return ExecutionResult(
                success=success,
                output=output,
                phase_complete=phase_complete,
                error=error,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False,
                output="",
                phase_complete=False,
                error="Claude CLI timed out after 15 minutes",
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                output="",
                phase_complete=False,
                error="claude CLI not found — is Claude Code installed?",
            )


# ── Codex executor (stub) ────────────────────────────────────────────────────

class CodexExecutor(Executor):
    """
    Stub for OpenAI Codex CLI backend.

    Mapping:
      CLAUDE.md   →  AGENTS.md
      skills      →  Codex skills (SKILL.md per command)
      subagents   →  Codex subagents
    """

    def name(self) -> str:
        return "codex"

    def run(self, prompt: str, cwd: Path) -> ExecutionResult:
        # TODO: implement when Codex CLI is available
        # 1. Write prompt → .codex/prompt.md
        # 2. Run: codex --agents-file AGENTS.md < .codex/prompt.md
        # 3. Parse output for PHASE_COMPLETE marker
        raise NotImplementedError(
            "Codex executor is not yet implemented. Use --executor claude-code."
        )


# ── factory ──────────────────────────────────────────────────────────────────

def get_executor(name: str = "claude-code") -> Executor:
    match name:
        case "claude-code":
            return ClaudeCodeExecutor()
        case "codex":
            return CodexExecutor()
        case _:
            raise ValueError(f"Unknown executor: {name}. Choose: claude-code | codex")


def executor_from_spec(project_dir: Path) -> Executor:
    spec_path = project_dir / "spec.yaml"
    if spec_path.exists():
        spec = yaml.safe_load(spec_path.read_text()) or {}
        return get_executor(spec.get("executor", "claude-code"))
    return ClaudeCodeExecutor()

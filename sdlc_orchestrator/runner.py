"""
Core orchestration loop.

Called by:
  - `sdlc run`            → run next executable phase then stop
  - `sdlc run --loop`     → run all phases, stop only at approval gates
  - hooks/on_stop.py      → advance state after Claude completes a turn
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console

from sdlc_orchestrator.executor import executor_from_spec, ExecutionResult
from sdlc_orchestrator.integrations import github, slack
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.phases import get_handler
from sdlc_orchestrator.state_machine import (
    State, WorkflowState, APPROVAL_STATES, STATE_LABELS
)
from sdlc_orchestrator.utils import sdlc_home

console = Console()


class Orchestrator:
    MAX_TEST_RETRIES = 3

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.workflow = WorkflowState(project_dir)
        self.memory = MemoryManager(project_dir)
        self.executor = executor_from_spec(project_dir)
        self.spec = self.memory.spec()
        self.repo = self.spec.get("repo", "")

    # ── public entry points ───────────────────────────────────────────────────

    def run_once(self) -> bool:
        """Run one executable phase. Returns True if loop should continue."""
        state = self.workflow.state

        if state == State.DONE:
            console.print("[green]✓ SDLC complete![/green]")
            return False

        if state in APPROVAL_STATES:
            console.print(f"[yellow]⏸  Waiting for human action:[/yellow] {STATE_LABELS[state]}")
            console.print(f"   Run: [bold]sdlc approve[/bold]  or  [bold]sdlc answer[/bold]")
            return False

        handler = get_handler(state)
        if not handler:
            console.print(f"[red]No handler for state: {state}[/red]")
            return False

        console.rule(f"[bold blue]Phase: {handler.phase_name}  |  State: {state.value}[/bold blue]")

        # Build prompt with injected memory
        prompt = handler.build_prompt(self.memory, self.project_dir)

        # Log prompt for debugging
        log_dir = sdlc_home(self.project_dir) / "workflow" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{handler.phase_name}_prompt.md").write_text(prompt)

        # Ensure we're on the right branch
        self._ensure_branch(f"sdlc/{handler.phase_name}")

        console.print(f"[dim]Calling {self.executor.name()} ...[/dim]")
        result = self.executor.run(prompt, self.project_dir)

        # Always save output log (log_dir already set above)
        (log_dir / f"{handler.phase_name}.log").write_text(result.output)

        if not result.success and not result.phase_complete:
            return self._handle_failure(handler, result)

        # Let handler post-process
        handler.post_run(result.output, self.memory, self.project_dir)

        # Commit whatever Claude produced
        self._git_commit(handler.phase_name)

        # Decide next state
        return self._advance(handler, result)

    def run_loop(self) -> None:
        """Run all phases until an approval gate or completion."""
        while self.run_once():
            pass

    # ── state advancement ─────────────────────────────────────────────────────

    def _advance(self, handler, result: ExecutionResult) -> bool:
        state = self.workflow.state

        # Check for PHASE_BLOCKED signal
        if "PHASE_BLOCKED:" in result.output:
            reason = result.output.split("PHASE_BLOCKED:", 1)[1].split("\n")[0].strip()
            if state == State.TEST_FAILURE_LOOP and self.workflow.retry_count < self.MAX_TEST_RETRIES:
                self.workflow.increment_retry()
                console.print(f"[yellow]Test failure retry {self.workflow.retry_count}/{self.MAX_TEST_RETRIES}[/yellow]")
                return True  # retry same state
            self.workflow.set_blocked(reason)
            self._notify(state.value, "blocked", reason)
            console.print(f"[red]BLOCKED:[/red] {reason}")
            return False

        # Feedback routing: feedback phase signals which phase to re-enter
        if state == State.FEEDBACK_INCORPORATION and "PHASE_COMPLETE: feedback:" in result.output:
            target_key = result.output.split("PHASE_COMPLETE: feedback:", 1)[1].split("\n")[0].strip()
            target_map = {
                "design": State.DESIGN_IN_PROGRESS,
                "plan":   State.TASK_PLAN_IN_PROGRESS,
                "code":   State.IMPLEMENTATION_IN_PROGRESS,
            }
            next_state = target_map.get(target_key, State.IMPLEMENTATION_IN_PROGRESS)
            self.workflow.transition(next_state)
            return True

        # Normal advancement
        next_state = handler.success_state
        self.workflow.transition(next_state)

        # Post-transition side effects
        if next_state in APPROVAL_STATES:
            self._open_pr(handler.phase_name)
            self._notify(handler.phase_name, "awaiting_approval")
            console.print(f"\n[yellow]⏸  Approval required for phase: {handler.phase_name}[/yellow]")
            console.print(f"   {STATE_LABELS[next_state]}")
            return False

        return True

    # ── git helpers ───────────────────────────────────────────────────────────

    def _ensure_branch(self, branch: str) -> None:
        try:
            existing = subprocess.run(
                ["git", "rev-parse", "--verify", branch],
                cwd=self.project_dir, capture_output=True
            ).returncode == 0
            cmd = ["git", "checkout", branch] if existing else ["git", "checkout", "-b", branch]
            subprocess.run(cmd, cwd=self.project_dir, capture_output=True)
            self.workflow._data["current_branch"] = branch
            self.workflow.save()
        except Exception:
            pass

    def _git_commit(self, phase_name: str) -> None:
        try:
            subprocess.run(["git", "add", "-A"], cwd=self.project_dir, capture_output=True)
            diff = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.project_dir, capture_output=True
            )
            if diff.returncode != 0:
                subprocess.run(
                    ["git", "commit", "-m", f"sdlc({phase_name}): phase output"],
                    cwd=self.project_dir, capture_output=True
                )
        except Exception:
            pass

    # ── integrations ──────────────────────────────────────────────────────────

    def _open_pr(self, phase_name: str) -> None:
        if not self.repo or not github.is_available():
            return
        branch = self.workflow._data.get("current_branch", f"sdlc/{phase_name}")
        try:
            subprocess.run(
                ["git", "push", "-u", "origin", branch],
                cwd=self.project_dir, capture_output=True
            )
        except Exception:
            pass
        pr_url = github.create_pr(
            repo=self.repo,
            phase=phase_name,
            branch=branch,
            body=(
                f"## SDLC Phase: `{phase_name}`\n\n"
                f"Automated output. Approve to advance.\n\n"
                f"Run `sdlc approve` when ready."
            ),
        )
        if pr_url:
            console.print(f"   PR: {pr_url}")

    def _notify(self, phase: str, event: str, extra: str = "") -> None:
        slack.notify_from_spec(self.spec, phase, event, extra)

    # ── failure handling ──────────────────────────────────────────────────────

    def _handle_failure(self, handler, result: ExecutionResult) -> bool:
        error = result.error or "executor returned non-zero"
        console.print(f"[red]Phase '{handler.phase_name}' failed:[/red] {error}")

        if self.workflow.retry_count < 2:
            self.workflow.increment_retry()
            console.print(f"[yellow]Retrying ({self.workflow.retry_count}/2)...[/yellow]")
            return True

        self.workflow.set_blocked(handler.on_blocked(error, self.memory))
        self._notify(handler.phase_name, "blocked", error)
        return False

"""
Agent Registry - manages multiple AI coding agents with automatic fallback.

Features:
- Tracks available agents (claude, kiro, codex, etc.)
- Monitors credit/quota status
- Automatic fallback when one agent fails
- Persistent state across sessions
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from sdlc_orchestrator.utils import sdlc_home


class AgentStatus(str, Enum):
    AVAILABLE = "available"
    NO_CREDITS = "no_credits"
    COOLDOWN = "cooldown"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass
class AgentInfo:
    """Information about a single agent."""
    name: str
    status: AgentStatus
    priority: int  # Lower = higher priority
    provider: Optional[str] = None
    supports_headless: bool = True
    last_used: Optional[str] = None
    last_error: Optional[str] = None
    last_credit_error: Optional[str] = None
    health_reason: Optional[str] = None
    cooldown_until: Optional[str] = None
    reset_at: Optional[str] = None
    failure_count: int = 0
    success_count: int = 0
    # Usage tracking
    total_api_calls: int = 0
    total_tokens_used: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    credits_remaining: Optional[int] = None
    credits_limit: Optional[int] = None
    daily_usage: dict = None  # date -> usage dict

    def __post_init__(self):
        if self.daily_usage is None:
            self.daily_usage = {}

    def to_dict(self) -> dict:
        return {
            **asdict(self),
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentInfo:
        data["status"] = AgentStatus(data["status"])
        data.setdefault("provider", _default_provider(data.get("name", "")))
        data.setdefault("supports_headless", True)
        data.setdefault("last_credit_error", None)
        data.setdefault("health_reason", None)
        data.setdefault("cooldown_until", None)
        data.setdefault("reset_at", None)
        # Usage tracking defaults
        data.setdefault("total_api_calls", 0)
        data.setdefault("total_tokens_used", 0)
        data.setdefault("total_input_tokens", 0)
        data.setdefault("total_output_tokens", 0)
        data.setdefault("estimated_cost_usd", 0.0)
        data.setdefault("credits_remaining", None)
        data.setdefault("credits_limit", None)
        data.setdefault("daily_usage", {})
        return cls(**data)


# Error patterns that indicate credit/quota issues
CREDIT_ERROR_PATTERNS = [
    r"credit.*exhausted",
    r"quota.*exceeded",
    r"rate.*limit",
    r"insufficient.*credits",
    r"billing.*issue",
    r"payment.*required",
    r"subscription.*expired",
    r"usage.*limit",
    r"429.*too many requests",
    r"overloaded",
]


class AgentRegistry:
    """
    Manages multiple AI coding agents with automatic fallback.

    Usage:
        registry = AgentRegistry(project_dir)
        result = registry.execute_with_fallback(skill="sdlc-requirement")
    """

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir.resolve()
        self._sdlc = sdlc_home(project_dir)
        self.registry_path = self._sdlc / "agent_registry.json"
        payload = self._load()
        self._agents: dict[str, AgentInfo] = payload["agents"]
        self._history: list[dict[str, Any]] = payload["history"]
        self._active_agent: Optional[str] = payload["active_agent"]

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        """Load agent registry from disk."""
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text())
                return {
                    "agents": {
                        name: AgentInfo.from_dict(info)
                        for name, info in data.get("agents", {}).items()
                    },
                    "history": list(data.get("history", [])),
                    "active_agent": data.get("active_agent"),
                }
            except Exception:
                pass
        return {
            "agents": self._defaults(),
            "history": [],
            "active_agent": None,
        }

    def _save(self) -> None:
        """Persist agent registry to disk."""
        data = {
            "agents": {name: agent.to_dict() for name, agent in self._agents.items()},
            "history": self._history[-100:],
            "active_agent": self._active_agent,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.registry_path.write_text(json.dumps(data, indent=2))

    def _defaults(self) -> dict[str, AgentInfo]:
        """Default agent configuration."""
        return {
            "claude-code": AgentInfo(
                name="claude-code",
                status=AgentStatus.AVAILABLE,
                priority=1,
                provider="anthropic",
            ),
            "kiro": AgentInfo(
                name="kiro",
                status=AgentStatus.AVAILABLE,
                priority=2,
                provider="kiro",
            ),
            "codex": AgentInfo(
                name="codex",
                status=AgentStatus.AVAILABLE,
                priority=3,
                provider="openai",
            ),
        }

    # ── agent management ─────────────────────────────────────────────────────

    def get_agent(self, name: str) -> Optional[AgentInfo]:
        """Get agent info by name."""
        self._refresh_time_based_statuses()
        return self._agents.get(name)

    def list_agents(self) -> list[AgentInfo]:
        """List all agents sorted by priority."""
        self._refresh_time_based_statuses()
        return sorted(self._agents.values(), key=lambda a: a.priority)

    def get_available_agents(self) -> list[AgentInfo]:
        """Get all available agents sorted by priority."""
        return [
            agent for agent in self.list_agents()
            if agent.status == AgentStatus.AVAILABLE
        ]

    def set_agent_status(
        self,
        name: str,
        status: AgentStatus,
        error: Optional[str] = None,
        *,
        health_reason: Optional[str] = None,
        cooldown_until: Optional[str] = None,
        reset_at: Optional[str] = None,
        last_credit_error: Optional[str] = None,
    ) -> None:
        """Update agent status."""
        if name in self._agents:
            agent = self._agents[name]
            agent.status = status
            if error:
                agent.last_error = error
            if health_reason is not None:
                agent.health_reason = health_reason
            if cooldown_until is not None:
                agent.cooldown_until = cooldown_until
            if reset_at is not None:
                agent.reset_at = reset_at
            if last_credit_error is not None:
                agent.last_credit_error = last_credit_error
            self._save()

    def mark_agent_used(self, name: str, success: bool = True) -> None:
        """Mark agent as used and update statistics."""
        if name in self._agents:
            agent = self._agents[name]
            agent.last_used = datetime.now(timezone.utc).isoformat()
            if success:
                agent.success_count += 1
                agent.failure_count = 0  # Reset failure count on success
                agent.status = AgentStatus.AVAILABLE
                agent.health_reason = None
                agent.cooldown_until = None
                self._active_agent = name
            else:
                agent.failure_count += 1
            self._save()

    def record_usage(
        self,
        name: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: Optional[float] = None,
        credits_remaining: Optional[int] = None,
    ) -> None:
        """Record API usage for an agent."""
        if name not in self._agents:
            return

        agent = self._agents[name]
        today = datetime.now(timezone.utc).date().isoformat()

        # Calculate cost if not provided
        if cost_usd is None:
            cost_usd = calculate_cost(agent.provider, input_tokens, output_tokens)

        # Update totals
        agent.total_api_calls += 1
        agent.total_input_tokens += input_tokens
        agent.total_output_tokens += output_tokens
        agent.total_tokens_used += (input_tokens + output_tokens)
        agent.estimated_cost_usd += cost_usd

        if credits_remaining is not None:
            agent.credits_remaining = credits_remaining

        # Update daily usage
        if today not in agent.daily_usage:
            agent.daily_usage[today] = {
                "api_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }

        daily = agent.daily_usage[today]
        daily["api_calls"] += 1
        daily["input_tokens"] += input_tokens
        daily["output_tokens"] += output_tokens
        daily["total_tokens"] += (input_tokens + output_tokens)
        daily["cost_usd"] += cost_usd

        # Keep only last 90 days of daily usage
        if len(agent.daily_usage) > 90:
            sorted_dates = sorted(agent.daily_usage.keys())
            for old_date in sorted_dates[:-90]:
                del agent.daily_usage[old_date]

        self._save()

    def add_agent(
        self,
        name: str,
        priority: int = 99,
        *,
        provider: Optional[str] = None,
        supports_headless: bool = True,
    ) -> None:
        """Add a new agent to the registry."""
        if name not in self._agents:
            self._agents[name] = AgentInfo(
                name=name,
                status=AgentStatus.AVAILABLE,
                priority=priority,
                provider=provider or _default_provider(name),
                supports_headless=supports_headless,
            )
            self._save()
        else:
            agent = self._agents[name]
            agent.priority = priority
            agent.provider = provider or agent.provider or _default_provider(name)
            agent.supports_headless = supports_headless
            self._save()

    def remove_agent(self, name: str) -> None:
        """Remove an agent from the registry."""
        if name in self._agents:
            del self._agents[name]
            if self._active_agent == name:
                self._active_agent = None
            self._save()

    def reprioritize(self, ordered_names: list[str]) -> None:
        """Apply a priority order to the registry."""
        seen: set[str] = set()
        priority = 1
        for name in ordered_names:
            if name not in self._agents or name in seen:
                continue
            self._agents[name].priority = priority
            priority += 1
            seen.add(name)
        for agent in self.list_agents():
            if agent.name in seen:
                continue
            self._agents[agent.name].priority = priority
            priority += 1
        self._save()

    def record_event(
        self,
        event_type: str,
        message: str,
        *,
        agent: Optional[str] = None,
        from_agent: Optional[str] = None,
        to_agent: Optional[str] = None,
        skill: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self._history.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "message": message,
                "agent": agent,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "skill": skill,
                "metadata": metadata or {},
            }
        )
        self._history = self._history[-100:]
        self._save()

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(reversed(self._history[-limit:]))

    @property
    def active_agent(self) -> Optional[str]:
        return self._active_agent

    # ── error detection ──────────────────────────────────────────────────────

    def is_credit_error(self, error_output: str) -> bool:
        """Check if error output indicates credit/quota issues."""
        error_lower = error_output.lower()
        return any(
            re.search(pattern, error_lower, re.IGNORECASE)
            for pattern in CREDIT_ERROR_PATTERNS
        )

    # ── execution with fallback ──────────────────────────────────────────────

    def execute_with_fallback(
        self,
        skill: str | None = None,
        preferred_agent: Optional[str] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Execute a skill with automatic fallback to other agents.

        Args:
            skill: The skill/command to execute
            preferred_agent: Preferred agent name (from spec.yaml)

        Returns:
            (success, agent_used, error_message)
        """
        from sdlc_orchestrator.backend import resolve_phase_skill
        from sdlc_orchestrator.memory import EXECUTOR_CLI, MemoryManager, executor_config
        from sdlc_orchestrator.state_machine import WorkflowState

        if not skill:
            skill = resolve_phase_skill(WorkflowState(self.project_dir).phase.value)

        # Get list of agents to try, starting with preferred if specified
        agents_to_try = []
        blocked_agent: Optional[str] = None

        if preferred_agent and preferred_agent in self._agents:
            pref = self._agents[preferred_agent]
            if pref.status == AgentStatus.AVAILABLE and pref.supports_headless:
                agents_to_try.append(pref)

        # Add all other available agents
        for agent in self.get_available_agents():
            if agent.name not in [a.name for a in agents_to_try] and agent.supports_headless:
                agents_to_try.append(agent)

        if not agents_to_try:
            return False, None, "No agents available - all exhausted or disabled"

        # Try each agent in order
        for agent in agents_to_try:
            cmd_template = EXECUTOR_CLI.get(agent.name)
            if not cmd_template:
                continue

            # Build command
            if agent.name == "codex":
                _, skills_dir, _ = executor_config(agent.name)
                skill_file = skills_dir / f"{skill}.md"
                prompt = skill_file.read_text() if skill_file.exists() else skill
                cmd = [part.replace("{skill}", prompt) for part in cmd_template]
            else:
                cmd = [part.replace("{skill}", skill) for part in cmd_template]

            # Execute
            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(self.project_dir),
                    capture_output=True,
                    text=True,
                )

                if result.returncode == 0:
                    # Success!
                    self.mark_agent_used(agent.name, success=True)
                    if blocked_agent and blocked_agent != agent.name:
                        self.record_event(
                            "fallback",
                            f"Switched from {blocked_agent} to {agent.name}",
                            from_agent=blocked_agent,
                            to_agent=agent.name,
                            skill=skill,
                        )
                    else:
                        self.record_event(
                            "execution_success",
                            f"{agent.name} executed {skill}",
                            agent=agent.name,
                            skill=skill,
                        )
                    return True, agent.name, None

                # Check if it's a credit error
                error_output = result.stderr + result.stdout
                if self.is_credit_error(error_output):
                    # Mark agent as out of credits and try next
                    self.set_agent_status(
                        agent.name,
                        AgentStatus.NO_CREDITS,
                        f"Credit exhausted: {error_output[:200]}",
                        health_reason="credit or quota exhausted",
                        last_credit_error=error_output[:500],
                    )
                    self.record_event(
                        "credit_exhausted",
                        f"{agent.name} hit a credit or quota limit",
                        agent=agent.name,
                        skill=skill,
                        metadata={"error": error_output[:500]},
                    )
                    blocked_agent = agent.name
                    continue
                else:
                    # Other error - still mark as used but don't disable
                    self.mark_agent_used(agent.name, success=False)
                    self.record_event(
                        "execution_failed",
                        f"{agent.name} failed while executing {skill}",
                        agent=agent.name,
                        skill=skill,
                        metadata={"error": error_output[:500]},
                    )

                    # If failure count is high, temporarily mark as error
                    if agent.failure_count >= 3:
                        self.set_agent_status(
                            agent.name,
                            AgentStatus.ERROR,
                            f"Multiple failures: {error_output[:200]}",
                            health_reason="multiple consecutive failures",
                        )

                    # Try next agent
                    continue

            except Exception as e:
                # Command execution failed
                self.mark_agent_used(agent.name, success=False)
                self.record_event(
                    "execution_exception",
                    f"{agent.name} could not be executed",
                    agent=agent.name,
                    skill=skill,
                    metadata={"error": str(e)},
                )
                continue

        # All agents failed
        self.record_event(
            "execution_aborted",
            f"All agents failed while executing {skill}",
            skill=skill,
        )
        return False, None, "All agents failed or out of credits"

    # ── utilities ────────────────────────────────────────────────────────────

    def reset_agent(self, name: str) -> None:
        """Reset an agent's status to available."""
        if name in self._agents:
            self._agents[name].status = AgentStatus.AVAILABLE
            self._agents[name].failure_count = 0
            self._agents[name].last_error = None
            self._agents[name].last_credit_error = None
            self._agents[name].health_reason = None
            self._agents[name].cooldown_until = None
            self._agents[name].reset_at = None
            self.record_event("agent_reset", f"{name} reset to available", agent=name)
            self._save()

    def reset_all(self) -> None:
        """Reset all agents to available status."""
        for agent in self._agents.values():
            agent.status = AgentStatus.AVAILABLE
            agent.failure_count = 0
            agent.last_error = None
            agent.last_credit_error = None
            agent.health_reason = None
            agent.cooldown_until = None
            agent.reset_at = None
        self.record_event("agent_reset_all", "All agents reset to available")
        self._save()

    def get_stats(self) -> dict:
        """Get registry statistics."""
        self._refresh_time_based_statuses()
        return {
            "total_agents": len(self._agents),
            "available": len(self.get_available_agents()),
            "active_agent": self._active_agent,
            "history": self.get_history(limit=10),
            "agents": {
                name: {
                    "status": agent.status.value,
                    "provider": agent.provider,
                    "supports_headless": agent.supports_headless,
                    "success_count": agent.success_count,
                    "failure_count": agent.failure_count,
                    "last_used": agent.last_used,
                    "last_error": agent.last_error,
                    "last_credit_error": agent.last_credit_error,
                    "health_reason": agent.health_reason,
                    "cooldown_until": agent.cooldown_until,
                    "reset_at": agent.reset_at,
                    "priority": agent.priority,
                    # Usage metrics
                    "total_api_calls": agent.total_api_calls,
                    "total_tokens_used": agent.total_tokens_used,
                    "total_input_tokens": agent.total_input_tokens,
                    "total_output_tokens": agent.total_output_tokens,
                    "estimated_cost_usd": agent.estimated_cost_usd,
                    "credits_remaining": agent.credits_remaining,
                    "credits_limit": agent.credits_limit,
                    "daily_usage": agent.daily_usage,
                }
                for name, agent in self._agents.items()
            },
        }

    def _refresh_time_based_statuses(self) -> None:
        now = datetime.now(timezone.utc)
        changed = False
        for agent in self._agents.values():
            release_at = _parse_ts(agent.cooldown_until) or _parse_ts(agent.reset_at)
            if release_at and release_at <= now and agent.status in {
                AgentStatus.NO_CREDITS,
                AgentStatus.COOLDOWN,
                AgentStatus.ERROR,
            }:
                agent.status = AgentStatus.AVAILABLE
                agent.cooldown_until = None
                agent.reset_at = None
                agent.health_reason = None
                changed = True
        if changed:
            self._save()


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _default_provider(name: str) -> Optional[str]:
    providers = {
        "claude-code": "anthropic",
        "codex": "openai",
        "kiro": "kiro",
        "cline": "cline",
        "cursor": "cursor",
        "windsurf": "windsurf",
    }
    return providers.get(name)


# Pricing per 1M tokens (approximate as of 2024)
PROVIDER_PRICING = {
    "anthropic": {
        "input": 3.00,  # $3 per 1M input tokens (Claude Sonnet)
        "output": 15.00,  # $15 per 1M output tokens
    },
    "openai": {
        "input": 2.50,  # $2.50 per 1M input tokens (GPT-4)
        "output": 10.00,  # $10 per 1M output tokens
    },
    "kiro": {
        "input": 0.0,  # Free tier or custom pricing
        "output": 0.0,
    },
    "cline": {
        "input": 3.00,
        "output": 15.00,
    },
    "cursor": {
        "input": 0.0,  # Subscription based
        "output": 0.0,
    },
    "windsurf": {
        "input": 0.0,  # Subscription based
        "output": 0.0,
    },
}


def calculate_cost(provider: Optional[str], input_tokens: int, output_tokens: int) -> float:
    """Calculate estimated cost in USD for token usage."""
    if not provider or provider not in PROVIDER_PRICING:
        return 0.0

    pricing = PROVIDER_PRICING[provider]
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost

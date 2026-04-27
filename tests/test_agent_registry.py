"""Tests for agent registry."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sdlc_orchestrator.agent_registry import AgentRegistry, AgentStatus


class AgentRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.project_dir = Path(self.tempdir.name) / "test_project"
        self.project_dir.mkdir()
        (self.project_dir / ".sdlc").mkdir()

    def test_agent_registry_initialization(self) -> None:
        registry = AgentRegistry(self.project_dir)

        agents = registry.list_agents()
        self.assertEqual(len(agents), 3)
        self.assertTrue(all(agent.status == AgentStatus.AVAILABLE for agent in agents))
        self.assertIsNotNone(registry.get_agent("claude-code"))
        self.assertIsNotNone(registry.get_agent("kiro"))
        self.assertIsNotNone(registry.get_agent("codex"))

    def test_agent_registry_persistence(self) -> None:
        registry = AgentRegistry(self.project_dir)
        registry.add_agent("test-agent", priority=10)

        registry2 = AgentRegistry(self.project_dir)
        agent = registry2.get_agent("test-agent")

        self.assertIsNotNone(agent)
        assert agent is not None
        self.assertEqual(agent.priority, 10)
        self.assertEqual(agent.status, AgentStatus.AVAILABLE)

    def test_mark_agent_used(self) -> None:
        registry = AgentRegistry(self.project_dir)

        registry.mark_agent_used("claude-code", success=True)
        agent = registry.get_agent("claude-code")
        assert agent is not None
        self.assertEqual(agent.success_count, 1)
        self.assertEqual(agent.failure_count, 0)

        registry.mark_agent_used("claude-code", success=False)
        agent = registry.get_agent("claude-code")
        assert agent is not None
        self.assertEqual(agent.success_count, 1)
        self.assertEqual(agent.failure_count, 1)

        registry.mark_agent_used("claude-code", success=True)
        agent = registry.get_agent("claude-code")
        assert agent is not None
        self.assertEqual(agent.failure_count, 0)

    def test_set_agent_status(self) -> None:
        registry = AgentRegistry(self.project_dir)
        registry.set_agent_status("claude-code", AgentStatus.NO_CREDITS, "Out of credits")

        agent = registry.get_agent("claude-code")
        assert agent is not None
        self.assertEqual(agent.status, AgentStatus.NO_CREDITS)
        self.assertEqual(agent.last_error, "Out of credits")

    def test_get_available_agents(self) -> None:
        registry = AgentRegistry(self.project_dir)
        self.assertEqual(len(registry.get_available_agents()), 3)

        registry.set_agent_status("kiro", AgentStatus.NO_CREDITS)
        available = registry.get_available_agents()

        self.assertEqual(len(available), 2)
        self.assertTrue(all(agent.name != "kiro" for agent in available))

    def test_reset_agent(self) -> None:
        registry = AgentRegistry(self.project_dir)
        registry.set_agent_status("claude-code", AgentStatus.ERROR, "Test error")
        registry.reset_agent("claude-code")

        agent = registry.get_agent("claude-code")
        assert agent is not None
        self.assertEqual(agent.status, AgentStatus.AVAILABLE)
        self.assertIsNone(agent.last_error)
        self.assertEqual(agent.failure_count, 0)

    def test_reset_all_agents(self) -> None:
        registry = AgentRegistry(self.project_dir)

        for name in ["claude-code", "kiro", "codex"]:
            registry.set_agent_status(name, AgentStatus.ERROR)

        registry.reset_all()

        for name in ["claude-code", "kiro", "codex"]:
            agent = registry.get_agent(name)
            assert agent is not None
            self.assertEqual(agent.status, AgentStatus.AVAILABLE)

    def test_is_credit_error(self) -> None:
        registry = AgentRegistry(self.project_dir)

        self.assertTrue(registry.is_credit_error("Error: credit limit exhausted"))
        self.assertTrue(registry.is_credit_error("Quota exceeded for this month"))
        self.assertTrue(registry.is_credit_error("Rate limit reached"))
        self.assertTrue(registry.is_credit_error("Insufficient credits"))
        self.assertTrue(registry.is_credit_error("429 Too Many Requests"))

        self.assertFalse(registry.is_credit_error("Syntax error in code"))
        self.assertFalse(registry.is_credit_error("File not found"))

    def test_add_remove_agent(self) -> None:
        registry = AgentRegistry(self.project_dir)
        registry.add_agent("new-agent", priority=5)
        self.assertIsNotNone(registry.get_agent("new-agent"))

        registry.remove_agent("new-agent")
        self.assertIsNone(registry.get_agent("new-agent"))

    def test_agent_priority_sorting(self) -> None:
        registry = AgentRegistry(self.project_dir)
        priorities = [agent.priority for agent in registry.list_agents()]
        self.assertEqual(priorities, sorted(priorities))

    def test_registry_stats(self) -> None:
        registry = AgentRegistry(self.project_dir)
        registry.set_agent_status("kiro", AgentStatus.NO_CREDITS)

        stats = registry.get_stats()

        self.assertEqual(stats["total_agents"], 3)
        self.assertEqual(stats["available"], 2)
        self.assertIn("claude-code", stats["agents"])
        self.assertEqual(stats["agents"]["kiro"]["status"], "no_credits")

    def test_registry_file_format(self) -> None:
        registry = AgentRegistry(self.project_dir)
        registry.mark_agent_used("claude-code", success=True)

        registry_path = self.project_dir / ".sdlc" / "agent_registry.json"
        self.assertTrue(registry_path.exists())

        data = json.loads(registry_path.read_text())

        self.assertIn("agents", data)
        self.assertIn("history", data)
        self.assertIn("active_agent", data)
        self.assertIn("last_updated", data)
        self.assertIn("claude-code", data["agents"])

    def test_registry_tracks_active_agent_and_history(self) -> None:
        registry = AgentRegistry(self.project_dir)

        registry.set_agent_status(
            "claude-code",
            AgentStatus.NO_CREDITS,
            "Out of credits",
            health_reason="credit exhausted",
            last_credit_error="quota exceeded",
        )
        registry.record_event("credit_exhausted", "claude-code hit quota", agent="claude-code")
        registry.mark_agent_used("codex", success=True)
        registry.record_event(
            "fallback",
            "Switched from claude-code to codex",
            from_agent="claude-code",
            to_agent="codex",
        )

        stats = registry.get_stats()

        self.assertEqual(stats["active_agent"], "codex")
        self.assertEqual(stats["agents"]["claude-code"]["last_credit_error"], "quota exceeded")
        self.assertEqual(stats["history"][0]["type"], "fallback")

    def test_reset_agent_clears_credit_metadata(self) -> None:
        registry = AgentRegistry(self.project_dir)

        registry.set_agent_status(
            "kiro",
            AgentStatus.COOLDOWN,
            "Wait until next reset",
            health_reason="daily quota reset pending",
            cooldown_until="2099-01-01T00:00:00+00:00",
            reset_at="2099-01-01T00:00:00+00:00",
            last_credit_error="quota exceeded",
        )

        registry.reset_agent("kiro")
        agent = registry.get_agent("kiro")

        assert agent is not None
        self.assertEqual(agent.status, AgentStatus.AVAILABLE)
        self.assertIsNone(agent.last_credit_error)
        self.assertIsNone(agent.cooldown_until)
        self.assertIsNone(agent.reset_at)


if __name__ == "__main__":
    unittest.main()

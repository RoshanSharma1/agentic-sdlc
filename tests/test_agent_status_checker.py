from __future__ import annotations

import io
import subprocess
import unittest
from unittest import mock

from sdlc_orchestrator.agent_status_checker import (
    check_claude_status,
    check_codex_status,
    check_kiro_status,
    get_recommended_agent,
)


class AgentStatusCheckerTests(unittest.TestCase):
    def test_claude_interactive_usage_marks_agent_exhausted_and_extracts_reset(self) -> None:
        def fake_run(cmd, capture_output=True, text=True, timeout=0):
            if cmd == ["claude", "--version"]:
                return subprocess.CompletedProcess(cmd, 0, "2.1.119 (Claude Code)\n", "")
            if cmd == ["claude", "auth", "status"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    '{"loggedIn": true, "authMethod": "claude.ai", "email": "dev@example.com"}',
                    "",
                )
            raise AssertionError(f"Unexpected command: {cmd}")

        interactive_output = (
            "Status Config Usage Stats\n\n"
            "Curretsession\n"
            "██████████████████████████████████████████████████100%used\n"
            "Reses3:10pm(America/Los_Angeles)\n\n"
            "Currentweek(allmodels)\n"
            "████████████████▌33%used\n"
            "ResetsApr27at3pm(America/Los_Angeles)\n"
        )

        with (
            mock.patch("sdlc_orchestrator.agent_status_checker.shutil.which", return_value="/usr/bin/claude"),
            mock.patch("sdlc_orchestrator.agent_status_checker.subprocess.run", side_effect=fake_run),
            mock.patch("sdlc_orchestrator.agent_status_checker._run_interactive_capture", return_value=interactive_output),
        ):
            status = check_claude_status()

        self.assertTrue(status.installed)
        self.assertTrue(status.authenticated)
        self.assertTrue(status.exhausted)
        self.assertEqual(status.state, "exhausted")
        self.assertEqual(status.next_reset_at, "3:10pm (America/Los_Angeles)")
        self.assertFalse(status.available)
        self.assertEqual(status.status_source, 'claude (interactive) -> /usage')
        self.assertEqual(
            [(window.label, window.used_percentage) for window in status.usage_windows],
            [("5h Usage", 100), ("Weekly Usage", 33)],
        )

    def test_claude_signed_out_when_auth_status_is_false(self) -> None:
        def fake_run(cmd, capture_output=True, text=True, timeout=0):
            if cmd == ["claude", "--version"]:
                return subprocess.CompletedProcess(cmd, 0, "2.1.118\n", "")
            if cmd == ["claude", "auth", "status"]:
                return subprocess.CompletedProcess(
                    cmd,
                    1,
                    '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty"}',
                    "",
                )
            raise AssertionError(f"Unexpected command: {cmd}")

        with (
            mock.patch("sdlc_orchestrator.agent_status_checker.shutil.which", return_value="/usr/bin/claude"),
            mock.patch("sdlc_orchestrator.agent_status_checker.subprocess.run", side_effect=fake_run),
        ):
            status = check_claude_status()

        self.assertTrue(status.installed)
        self.assertFalse(status.authenticated)
        self.assertEqual(status.state, "signed_out")
        self.assertFalse(status.available)
        self.assertEqual(status.interactive_usage_command, "/usage")

    def test_kiro_usage_marks_agent_exhausted_and_extracts_reset(self) -> None:
        def fake_run(cmd, capture_output=True, text=True, timeout=0):
            if cmd == ["kiro-cli", "--version"]:
                return subprocess.CompletedProcess(cmd, 0, "kiro-cli 1.26.0\n", "")
            if cmd == ["kiro-cli", "whoami", "--format", "json"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    '{"accountType": "SocialGoogle", "email": "dev@example.com"}',
                    "",
                )
            if cmd == ["kiro-cli", "chat", "--no-interactive", "/usage"]:
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    (
                        "Estimated Usage | resets on 2026-05-01 | KIRO FREE\n\n"
                        "🎁 Bonus credits: 500.00/500 credits used, expires in 21 days\n\n"
                        "Credits (50.00 of 50 covered in plan)\n"
                        "████████████████ 100%\n"
                        "Overages: Disabled\n"
                    ),
                    "",
                )
            raise AssertionError(f"Unexpected command: {cmd}")

        with (
            mock.patch("sdlc_orchestrator.agent_status_checker.shutil.which", return_value="/usr/bin/kiro-cli"),
            mock.patch("sdlc_orchestrator.agent_status_checker.subprocess.run", side_effect=fake_run),
        ):
            status = check_kiro_status()

        self.assertTrue(status.installed)
        self.assertTrue(status.authenticated)
        self.assertTrue(status.exhausted)
        self.assertEqual(status.state, "exhausted")
        self.assertEqual(status.next_reset_at, "2026-05-01")
        self.assertEqual(status.subscription_tier, "KIRO FREE")
        self.assertFalse(status.available)
        self.assertEqual(
            [(window.label, window.used_percentage) for window in status.usage_windows],
            [("Plan Usage", 100), ("Bonus Usage", 100)],
        )

    def test_codex_cached_status_extracts_usage_windows(self) -> None:
        def fake_run(cmd, capture_output=True, text=True, timeout=0):
            if cmd == ["codex", "--version"]:
                return subprocess.CompletedProcess(cmd, 0, "codex-cli 0.125.0\n", "")
            if cmd == ["codex", "login", "status"]:
                return subprocess.CompletedProcess(cmd, 0, "Logged in using ChatGPT\n", "")
            raise AssertionError(f"Unexpected command: {cmd}")

        cached_status = (
            "Visit https://chatgpt.com/codex/settings/usage for up-to-date information on rate limits and credits\n"
            "5h limit: [█████████░░░░░░░░░░░] 47% left (resets 17:30)\n"
            "Weekly limit: [███████░░░░░░░░░░░░░] 33% left\n"
            "(resets 20:38 on 28 Apr)\n"
        )

        with (
            mock.patch("sdlc_orchestrator.agent_status_checker.shutil.which", return_value="/usr/bin/codex"),
            mock.patch("sdlc_orchestrator.agent_status_checker.subprocess.run", side_effect=fake_run),
            mock.patch("sdlc_orchestrator.agent_status_checker._read_codex_rate_limits_via_app_server", return_value=(None, "")),
            mock.patch("sdlc_orchestrator.agent_status_checker._run_interactive_capture", return_value=""),
            mock.patch("sdlc_orchestrator.agent_status_checker._read_codex_status_cache", return_value=cached_status),
        ):
            status = check_codex_status()

        self.assertTrue(status.installed)
        self.assertTrue(status.authenticated)
        self.assertFalse(status.exhausted)
        self.assertEqual(status.state, "ready")
        self.assertEqual(status.next_reset_at, "17:30")
        self.assertEqual(
            [(window.label, window.used_percentage) for window in status.usage_windows],
            [("5h Usage", 53), ("Weekly Usage", 67)],
        )
        self.assertEqual(status.interactive_status_command, "/status")

    def test_codex_cached_status_extracts_usage_windows_without_bracket_bars(self) -> None:
        def fake_run(cmd, capture_output=True, text=True, timeout=0):
            if cmd == ["codex", "--version"]:
                return subprocess.CompletedProcess(cmd, 0, "codex-cli 0.125.0\n", "")
            if cmd == ["codex", "login", "status"]:
                return subprocess.CompletedProcess(cmd, 0, "Logged in using ChatGPT\n", "")
            raise AssertionError(f"Unexpected command: {cmd}")

        cached_status = (
            "OpenAI Codex\n"
            "Visit https://chatgpt.com/codex/settings/usage for up-to-date information on rate limits and credits\n"
            "5h limit: 15% left (resets 5:30 PM)\n"
            "Weekly limit: 28% left\n"
            "(resets 8:38 PM on 28 Apr)\n"
        )

        with (
            mock.patch("sdlc_orchestrator.agent_status_checker.shutil.which", return_value="/usr/bin/codex"),
            mock.patch("sdlc_orchestrator.agent_status_checker.subprocess.run", side_effect=fake_run),
            mock.patch("sdlc_orchestrator.agent_status_checker._read_codex_rate_limits_via_app_server", return_value=(None, "")),
            mock.patch("sdlc_orchestrator.agent_status_checker._run_interactive_capture", return_value=""),
            mock.patch("sdlc_orchestrator.agent_status_checker._read_codex_status_cache", return_value=cached_status),
        ):
            status = check_codex_status()

        self.assertEqual(
            [(window.label, window.used_percentage, window.reset_at) for window in status.usage_windows],
            [("5h Usage", 85, "5:30 PM"), ("Weekly Usage", 72, "8:38 PM on 28 Apr")],
        )
        self.assertEqual(status.next_reset_at, "5:30 PM")

    def test_codex_app_server_rate_limits_populate_usage_windows(self) -> None:
        def fake_run(cmd, capture_output=True, text=True, timeout=0):
            if cmd == ["codex", "--version"]:
                return subprocess.CompletedProcess(cmd, 0, "codex-cli 0.125.0\n", "")
            if cmd == ["codex", "login", "status"]:
                return subprocess.CompletedProcess(cmd, 0, "Logged in using ChatGPT\n", "")
            raise AssertionError(f"Unexpected command: {cmd}")

        app_server_output = """< {
<   "method": "account/rateLimits/updated",
<   "params": {
<     "rateLimits": {
<       "limitId": "codex",
<       "primary": {
<         "usedPercent": 23,
<         "windowDurationMins": 300,
<         "resetsAt": 1777270020
<       },
<       "secondary": {
<         "usedPercent": 78,
<         "windowDurationMins": 10080,
<         "resetsAt": 1777433904
<       },
<       "planType": "plus",
<       "rateLimitReachedType": null
<     }
<   }
< }
"""

        class FakeProcess:
            def __init__(self, output: str) -> None:
                self.stdout = io.StringIO(output)

            def poll(self):
                position = self.stdout.tell()
                self.stdout.seek(0, io.SEEK_END)
                end = self.stdout.tell()
                self.stdout.seek(position)
                return 0 if position >= end else None

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return 0

            def kill(self):
                return None

        with (
            mock.patch("sdlc_orchestrator.agent_status_checker.shutil.which", return_value="/usr/bin/codex"),
            mock.patch("sdlc_orchestrator.agent_status_checker.subprocess.run", side_effect=fake_run),
            mock.patch(
                "sdlc_orchestrator.agent_status_checker.subprocess.Popen",
                return_value=FakeProcess(app_server_output),
            ),
        ):
            status = check_codex_status()

        self.assertEqual(status.state, "ready")
        self.assertEqual(status.subscription_tier, "PLUS")
        self.assertEqual(
            [(window.label, window.used_percentage) for window in status.usage_windows],
            [("5h Usage", 23), ("Weekly Usage", 78)],
        )
        self.assertTrue(status.next_reset_at)

    def test_recommended_agent_prefers_ready_over_unknown(self) -> None:
        with mock.patch(
            "sdlc_orchestrator.agent_status_checker.check_all_agents",
            return_value={
                "claude-code": mock.Mock(state="unknown", available=True),
                "kiro": mock.Mock(state="exhausted", available=False),
                "codex": mock.Mock(state="ready", available=True),
            },
        ):
            self.assertEqual(get_recommended_agent(), "codex")


if __name__ == "__main__":
    unittest.main()

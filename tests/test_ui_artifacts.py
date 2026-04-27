from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from sdlc_orchestrator import backend
from sdlc_orchestrator.agent_registry import AgentRegistry, AgentStatus
from sdlc_orchestrator.backend import BackendStore, OrchestratorRuntime
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import Phase, Status, WorkflowState
from sdlc_orchestrator.ui import server
from sdlc_orchestrator.ui.server import _project_data, _resolve_chat_executor, _runtime_meta


class UIArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_runtime = backend._default_runtime
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        backend._default_runtime = OrchestratorRuntime(BackendStore(Path(self._tempdir.name) / "backend.sqlite3"))
        self.addCleanup(self._restore_runtime)
        server._agent_status_cache_payload = None
        server._agent_status_cache_updated_at = 0.0

    def _restore_runtime(self) -> None:
        backend._default_runtime = self._old_runtime

    def _make_project(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        project_dir = Path(tempdir.name) / "customer-portal"
        project_dir.mkdir()
        MemoryManager(project_dir).write_spec({"project_name": "Customer Portal"})
        return project_dir

    def test_project_payload_exposes_all_testing_artifacts(self) -> None:
        project_dir = self._make_project()
        wf = WorkflowState(project_dir)
        wf._data["phase"] = Phase.TESTING.value
        wf._data["status"] = Status.IN_PROGRESS.value
        wf._data["phases"][Phase.REQUIREMENT.value]["status"] = Status.DONE.value
        wf._data["phases"][Phase.DESIGN.value]["status"] = Status.DONE.value
        wf._data["phases"][Phase.PLANNING.value]["status"] = Status.DONE.value
        wf._data["phases"][Phase.IMPLEMENTATION.value]["status"] = Status.DONE.value
        wf._data["phases"][Phase.REQUIREMENT.value]["status"] = Status.DONE.value
        wf._data["phases"][Phase.TESTING.value]["status"] = Status.IN_PROGRESS.value
        wf.mark_artifact("requirements", "docs/sdlc/customer-portal/requirements.md")
        wf.mark_artifact("test_spec", "docs/sdlc/test-spec.md")
        wf.mark_artifact("test_cases", "test-cases.md")
        wf.mark_artifact("test_results", "docs/sdlc/test-results.md")

        data = _project_data(project_dir, "customer-portal")

        testing_keys = [item["key"] for item in data["artifact_items"] if item["group"] == "testing"]
        self.assertEqual(
            testing_keys,
            ["test_cases", "test_results"],
        )

        requirement_phase = next(ph for ph in data["phases"] if ph["name"] == Phase.REQUIREMENT.value)
        testing_phase = next(ph for ph in data["phases"] if ph["name"] == Phase.TESTING.value)

        self.assertEqual(
            [item["key"] for item in requirement_phase["artifact_items"]],
            ["requirements", "test_spec"],
        )
        self.assertEqual(
            [item["key"] for item in testing_phase["artifact_items"]],
            ["test_cases", "test_results"],
        )

    def test_runtime_meta_reports_active_source(self) -> None:
        meta = _runtime_meta()

        self.assertIn(meta["source_mode"], {"repo", "installed"})
        self.assertTrue(meta["package_root"].endswith("sdlc_orchestrator"))
        self.assertTrue(meta["server_file"].endswith("ui/server.py"))
        self.assertTrue(meta["ui_entry_file"].endswith("ui/react-app/dist/index.html"))

    def test_ui_payload_normalizes_legacy_testing_artifacts_per_project(self) -> None:
        first = self._make_project()
        second = first.parent / "admin-console"
        second.mkdir()
        MemoryManager(second).write_spec({"project_name": "Admin Console"})

        first_wf = WorkflowState(first)
        first_wf._data["artifacts"]["test_results"] = "docs/sdlc/test-results.md"
        first_wf.save()

        second_wf = WorkflowState(second)
        second_wf._data["artifacts"]["test_results"] = "docs/sdlc/test-results.md"
        second_wf.save()

        first_data = _project_data(first, "customer-portal")
        second_data = _project_data(second, "admin-console")
        first_artifacts = {item["key"]: item for item in first_data["artifact_items"]}
        second_artifacts = {item["key"]: item for item in second_data["artifact_items"]}

        self.assertEqual(
            first_artifacts["test_results"]["url"],
            "/api/projects/customer-portal/artifact/test_results",
        )
        self.assertEqual(
            second_artifacts["test_results"]["url"],
            "/api/projects/admin-console/artifact/test_results",
        )
        self.assertNotEqual(first_data["name"], second_data["name"])
        self.assertEqual(
            WorkflowState(first).artifacts["test_results"],
            "docs/sdlc/customer-portal/test-results.md",
        )
        self.assertEqual(
            WorkflowState(second).artifacts["test_results"],
            "docs/sdlc/admin-console/test-results.md",
        )

    def test_project_payload_includes_agent_registry(self) -> None:
        project_dir = self._make_project()
        registry = AgentRegistry(project_dir)
        registry.set_agent_status(
            "claude-code",
            AgentStatus.NO_CREDITS,
            "Out of credits",
            health_reason="credit exhausted",
            last_credit_error="quota exceeded",
        )
        registry.mark_agent_used("codex", success=True)
        registry.record_event("fallback", "Switched from claude-code to codex", from_agent="claude-code", to_agent="codex")

        data = _project_data(project_dir, "customer-portal")

        self.assertIn("agent_registry", data)
        self.assertEqual(data["agent_registry"]["active_agent"], "codex")
        self.assertEqual(data["agent_registry"]["agents"][0]["name"], "claude-code")
        self.assertEqual(data["agent_registry"]["history"][0]["type"], "fallback")

    def test_start_project_endpoint_bootstraps_worktree_and_registry(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        repo_root = Path(tempdir.name) / "repo"
        repo_root.mkdir()

        original_project_dir = server._project_dir
        server._project_dir = repo_root
        self.addCleanup(setattr, server, "_project_dir", original_project_dir)

        def fake_ensure_worktree(root: Path, slug: str):
            worktree = root / "worktree" / slug
            worktree.mkdir(parents=True, exist_ok=True)
            return worktree, f"worktree/{slug}"

        original_ensure_worktree = server._ensure_worktree
        server._ensure_worktree = fake_ensure_worktree
        self.addCleanup(setattr, server, "_ensure_worktree", original_ensure_worktree)

        original_dispatch_current_phase = server._dispatch_current_phase
        server._dispatch_current_phase = lambda project_dir, trigger: {
            "ok": True,
            "phase": "requirement",
            "status": "in_progress",
            "job_id": "job-1234",
            "run_id": "run-1234",
            "agent_name": "codex",
            "skill": "sdlc-requirement",
            "job_status": "running",
        }
        self.addCleanup(setattr, server, "_dispatch_current_phase", original_dispatch_current_phase)

        client = TestClient(server.app)
        response = client.post(
            "/api/projects/start",
            json={
                "project_name": "Customer Portal",
                "description": "A B2B customer self-service workspace.",
                "tech_stack": "FastAPI + React",
                "executor": "codex",
                "agent_order": "codex, claude-code, kiro",
                "require_approvals": False,
                "agent_fallback": True,
                "start_pipeline": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        project_dir = repo_root / "worktree" / "customer-portal"
        spec = MemoryManager(project_dir).spec()
        registry = AgentRegistry(project_dir)

        self.assertEqual(spec["project_name"], "Customer Portal")
        self.assertEqual(spec["executor"], "codex")
        self.assertFalse(any(spec["phase_approvals"].values()))
        self.assertEqual(WorkflowState(project_dir)._data["base_branch"], "worktree/customer-portal")
        self.assertEqual(registry.list_agents()[0].name, "codex")
        self.assertEqual(payload["pipeline"]["agent_name"], "codex")
        self.assertEqual(payload["pipeline"]["skill"], "sdlc-requirement")

    def test_start_project_endpoint_persists_intake_metadata(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        repo_root = Path(tempdir.name) / "repo"
        repo_root.mkdir()
        source_file = Path(tempdir.name) / "prd.md"
        source_file.write_text("# Customer Portal PRD")

        original_project_dir = server._project_dir
        server._project_dir = repo_root
        self.addCleanup(setattr, server, "_project_dir", original_project_dir)

        def fake_ensure_worktree(root: Path, slug: str):
            worktree = root / "worktree" / slug
            worktree.mkdir(parents=True, exist_ok=True)
            return worktree, f"worktree/{slug}"

        original_ensure_worktree = server._ensure_worktree
        server._ensure_worktree = fake_ensure_worktree
        self.addCleanup(setattr, server, "_ensure_worktree", original_ensure_worktree)

        client = TestClient(server.app)
        response = client.post(
            "/api/projects/start",
            json={
                "project_name": "Customer Portal",
                "description": "A B2B customer self-service workspace.",
                "tech_stack": "FastAPI + React",
                "executor": "codex",
                "source_type": "prd_file",
                "source_label": "Portal PRD",
                "source_path": str(source_file),
                "repo_mode": "existing",
                "repo_provider": "github",
                "repo": "acme/customer-portal",
            },
        )

        self.assertEqual(response.status_code, 200)
        project = response.json()["project"]
        self.assertEqual(project["source"]["type"], "prd_file")
        self.assertEqual(project["source"]["label"], "Portal PRD")
        self.assertEqual(project["repo_binding"]["repo_name"], "acme/customer-portal")
        self.assertEqual(project["repo_binding"]["provider"], "github")

        intake = client.get("/api/projects/customer-portal/intake")
        self.assertEqual(intake.status_code, 200)
        payload = intake.json()
        self.assertEqual(payload["source"]["location"], str(source_file))
        self.assertIn("Customer Portal PRD", payload["source"]["content_text"])
        self.assertEqual(payload["repo_binding"]["mode"], "existing")

    def test_github_repo_endpoints_use_integration_helpers(self) -> None:
        client = TestClient(server.app)

        with (
            mock.patch("sdlc_orchestrator.ui.server.github.is_available", return_value=True),
            mock.patch(
                "sdlc_orchestrator.ui.server.github.list_repositories",
                return_value=[{"full_name": "acme/customer-portal", "url": "https://github.com/acme/customer-portal"}],
            ),
            mock.patch(
                "sdlc_orchestrator.ui.server.github.create_repository",
                return_value={
                    "name": "customer-portal",
                    "full_name": "acme/customer-portal",
                    "url": "https://github.com/acme/customer-portal",
                    "visibility": "private",
                    "description": "",
                },
            ),
        ):
            list_response = client.get("/api/intake/github/repos")
            create_response = client.post(
                "/api/intake/github/repos",
                json={"name": "customer-portal", "description": "Portal repo", "private": True},
            )

        self.assertEqual(list_response.status_code, 200)
        self.assertTrue(list_response.json()["available"])
        self.assertEqual(list_response.json()["repos"][0]["full_name"], "acme/customer-portal")

        self.assertEqual(create_response.status_code, 200)
        self.assertEqual(create_response.json()["repo"]["full_name"], "acme/customer-portal")

    def test_fs_browse_can_include_files_for_intake_picker(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        root = Path(tempdir.name)
        (root / "docs").mkdir()
        (root / "docs" / "prd.md").write_text("# PRD")

        client = TestClient(server.app)
        response = client.get("/api/fs/browse", params={"path": str(root / "docs"), "include_files": True})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("prd.md", payload["files"])
        self.assertTrue(any(entry["is_file"] and entry["name"] == "prd.md" for entry in payload["entries"]))

    def test_agent_reset_endpoint_returns_updated_registry(self) -> None:
        project_dir = self._make_project()
        registry = AgentRegistry(project_dir)
        registry.set_agent_status("claude-code", AgentStatus.NO_CREDITS, "Out of credits")

        original_resolve = server._resolve_project_dir
        server._resolve_project_dir = lambda name: project_dir
        self.addCleanup(setattr, server, "_resolve_project_dir", original_resolve)

        client = TestClient(server.app)
        response = client.post("/api/projects/customer-portal/agents/claude-code/reset")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["registry"]["agents"][0]["status"], "available")

    def test_projects_endpoint_reads_db_backed_catalog(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        repo_root = Path(tempdir.name) / "repo"
        repo_root.mkdir()

        active = repo_root / "worktree" / "customer-portal"
        active.mkdir(parents=True)
        MemoryManager(active).write_spec({"project_name": "Customer Portal"})
        wf_active = WorkflowState(active)
        wf_active._data["phase"] = Phase.PLANNING.value
        wf_active._data["status"] = Status.IN_PROGRESS.value
        wf_active.save()

        archived = repo_root / ".projects" / "legacy-portal"
        archived.mkdir(parents=True)
        MemoryManager(archived).write_spec({"project_name": "Legacy Portal"})
        wf_archived = WorkflowState(archived)
        wf_archived._data["phase"] = Phase.DONE.value
        wf_archived._data["status"] = Status.DONE.value
        wf_archived.save()

        original_roots = server._global_repo_roots
        server._global_repo_roots = lambda: [repo_root]
        self.addCleanup(setattr, server, "_global_repo_roots", original_roots)

        client = TestClient(server.app)
        response = client.get("/api/projects")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["active"][0]["name"], "customer-portal")
        self.assertEqual(payload["closed"][0]["name"], "legacy-portal")
        self.assertTrue(payload["closed"][0]["archived"])

    def test_chat_executor_resolves_from_project_spec(self) -> None:
        project_dir = self._make_project()
        MemoryManager(project_dir).write_spec({"project_name": "Customer Portal", "executor": "codex"})

        meta = _resolve_chat_executor(project_dir, "codex")

        self.assertEqual(meta["executor"], "codex")
        self.assertEqual(meta["requested_executor"], "codex")
        self.assertEqual(meta["resolved_executor"], "codex")
        self.assertEqual(meta["label"], "Codex")
        self.assertIn("Message Codex", meta["placeholder"])

    def test_chat_executor_keeps_auto_as_requested_mode(self) -> None:
        project_dir = self._make_project()
        MemoryManager(project_dir).write_spec({"project_name": "Customer Portal", "executor": "codex"})

        meta = _resolve_chat_executor(project_dir, "auto")

        self.assertEqual(meta["requested_executor"], "auto")
        self.assertEqual(meta["resolved_executor"], "codex")

    def test_agent_status_endpoint_uses_five_minute_cache(self) -> None:
        def make_status(**overrides):
            base = {
                "name": "claude-code",
                "available": True,
                "installed": True,
                "authenticated": True,
                "exhausted": False,
                "state": "ready",
                "next_reset_at": "3:10pm",
                "version": "2.1.119",
                "credits_remaining": None,
                "credits_limit": None,
                "subscription_tier": None,
                "rate_limit_remaining": None,
                "auth_method": "claude.ai",
                "account_label": "dev@example.com",
                "status_command": None,
                "interactive_status_command": "/status",
                "interactive_usage_command": "/usage",
                "status_source": None,
                "status_details": None,
                "notes": None,
                "error_message": None,
                "last_checked": "now",
                "total_api_calls": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "daily_usage": None,
                "weekly_usage": None,
                "monthly_usage": None,
                "next_reset_date": "3:10pm",
                "billing_cycle": None,
                "usage_windows": [],
            }
            base.update(overrides)
            return SimpleNamespace(**base)

        first_statuses = {
            "claude-code": make_status(),
            "kiro": make_status(
                name="kiro",
                available=False,
                exhausted=True,
                state="exhausted",
                next_reset_at="2026-05-01",
                version="2.1.1",
                subscription_tier="KIRO FREE",
                auth_method="SocialGoogle",
                interactive_status_command=None,
                next_reset_date="2026-05-01",
            ),
            "codex": make_status(
                name="codex",
                available=False,
                exhausted=None,
                state="unknown",
                next_reset_at=None,
                version="0.125.0",
                auth_method="ChatGPT",
                account_label=None,
                interactive_status_command="/status",
                interactive_usage_command="/status",
                next_reset_date=None,
            ),
        }

        second_statuses = {
            **first_statuses,
            "claude-code": make_status(available=False, exhausted=True, state="exhausted"),
        }

        client = TestClient(server.app)
        monotonic_values = iter([100.0, 100.0, 399.0, 401.0, 401.0, 701.0])

        with (
            mock.patch("sdlc_orchestrator.ui.server._agent_status_cache_now", side_effect=lambda: next(monotonic_values)),
            mock.patch(
                "sdlc_orchestrator.agent_status_checker.get_agent_usage_stats",
                side_effect=[first_statuses, second_statuses],
            ) as mocked_stats,
        ):
            first = client.get("/api/agents/status")
            second = client.get("/api/agents/status")
            third = client.get("/api/agents/status")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 200)
        self.assertEqual(first.json()["agents"]["claude-code"]["state"], "ready")
        self.assertEqual(second.json()["agents"]["claude-code"]["state"], "ready")
        self.assertEqual(third.json()["agents"]["claude-code"]["state"], "exhausted")
        self.assertEqual(mocked_stats.call_count, 2)

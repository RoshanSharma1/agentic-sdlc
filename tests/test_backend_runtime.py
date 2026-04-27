from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from sdlc_orchestrator import backend
from sdlc_orchestrator.backend import BackendStore, EventType, OrchestratorRuntime
from sdlc_orchestrator.memory import MemoryManager
from sdlc_orchestrator.state_machine import Phase, Status, WorkflowState
from sdlc_orchestrator.utils import project_slug


class BackendRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.project_dir = Path(self.tempdir.name) / "chorus-project"
        self.project_dir.mkdir()
        (self.project_dir / ".sdlc").mkdir()

        self.db_path = Path(self.tempdir.name) / "backend.sqlite3"
        self.store = BackendStore(self.db_path)
        self.runtime = OrchestratorRuntime(self.store)

        self._old_runtime = backend._default_runtime
        backend._default_runtime = self.runtime
        self.addCleanup(self._restore_runtime)

    def _restore_runtime(self) -> None:
        backend._default_runtime = self._old_runtime

    def _write_spec(self) -> None:
        MemoryManager(self.project_dir).write_spec(
            {
                "project_name": "Chorus Project",
                "description": "Backend rewrite",
                "tech_stack": "Python",
                "repo": "owner/repo",
                "executor": "codex",
                "agent_fallback": True,
                "phase_approvals": {
                    "requirement": True,
                    "design": True,
                    "planning": True,
                    "implementation": True,
                    "testing": True,
                    "documentation": True,
                },
            }
        )

    def _write_spec_with_job_agents(self) -> None:
        MemoryManager(self.project_dir).write_spec(
            {
                "project_name": "Chorus Project",
                "description": "Backend rewrite",
                "tech_stack": "Python",
                "repo": "owner/repo",
                "executor": "codex",
                "job_agents": {
                    "planning": "kiro",
                    "implementation": "claude-code",
                },
                "agent_fallback": True,
                "phase_approvals": {
                    "requirement": True,
                    "design": True,
                    "planning": True,
                    "implementation": True,
                    "testing": True,
                    "documentation": True,
                },
            }
        )

    def test_sync_project_persists_metadata(self) -> None:
        self._write_spec()

        record = self.store.sync_project(
            self.project_dir,
            workflow_data={
                "phase": "planning",
                "status": "in_progress",
                "base_branch": "worktree/chorus-project",
                "current_branch": "sdlc-chorus-project-plan",
            },
        )

        self.assertEqual(record.slug, project_slug(self.project_dir))
        self.assertEqual(record.name, "Chorus Project")
        self.assertEqual(record.executor, "codex")
        self.assertEqual(record.phase, "planning")
        self.assertEqual(record.current_branch, "sdlc-chorus-project-plan")

    def test_record_approval_publishes_event(self) -> None:
        self._write_spec()
        self.store.sync_project(self.project_dir, workflow_data={"phase": "design", "status": "awaiting_approval"})

        events: list = []
        runtime = OrchestratorRuntime(self.store)
        runtime.subscribe(events.append)

        approval = runtime.record_approval(
            self.project_dir,
            phase="design",
            source="github_webhook",
            state="approved",
            payload={"branch": "sdlc-chorus-project-design"},
        )

        self.assertEqual(approval.phase, "design")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.APPROVAL_RECEIVED)
        self.assertEqual(events[0].payload["state"], "approved")

    def test_spawn_agent_publishes_started_and_finished_events(self) -> None:
        self._write_spec()
        self.store.sync_project(self.project_dir, workflow_data={"phase": "implementation", "status": "in_progress"})

        seen: list = []
        finished = threading.Event()

        def handler(event) -> None:
            seen.append(event)
            if event.type == EventType.AGENT_RUN_FINISHED:
                finished.set()

        self.runtime.subscribe(handler)
        run = self.runtime.spawn_agent(
            project_dir=self.project_dir,
            agent_name="test-agent",
            skill="unit-test-skill",
            command=[sys.executable, "-c", "print('hello from agent')"],
            trigger="unit_test",
        )

        self.assertTrue(finished.wait(timeout=5), "agent run did not finish")

        records = self.store.recent_agent_runs(project_slug(self.project_dir), limit=1)
        self.assertEqual(records[0].id, run.id)
        self.assertEqual(records[0].status, "succeeded")
        self.assertEqual([event.type for event in seen], [EventType.AGENT_RUN_STARTED, EventType.AGENT_RUN_FINISHED])

    def test_workflow_save_syncs_metadata_into_db(self) -> None:
        self._write_spec()

        wf = WorkflowState(self.project_dir)
        wf._data["phase"] = Phase.TESTING.value
        wf._data["status"] = Status.IN_PROGRESS.value
        wf._data["base_branch"] = "worktree/chorus-project"
        wf._data["current_branch"] = "sdlc-chorus-project-testing"
        wf.save()

        record = self.store.get_project(project_slug(self.project_dir))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.phase, Phase.TESTING.value)
        self.assertEqual(record.status, Status.IN_PROGRESS.value)
        self.assertEqual(record.current_branch, "sdlc-chorus-project-testing")

    def test_spec_and_workflow_can_fall_back_to_db_snapshot(self) -> None:
        spec = {
            "project_name": "Chorus Project",
            "description": "DB-backed context",
            "tech_stack": "Python",
            "repo": "owner/repo",
            "executor": "claude-code",
            "phase_approvals": {"requirement": True},
        }
        workflow = {
            "phase": Phase.DESIGN.value,
            "status": Status.AWAITING_APPROVAL.value,
            "base_branch": "worktree/chorus-project",
            "current_branch": "sdlc-chorus-project-design",
            "phases": {},
            "artifacts": {},
            "history": [],
            "process": {"pid": None, "last_tick": None, "held": False},
        }
        self.store.sync_project(self.project_dir, spec=spec, workflow_data=workflow)

        self.assertEqual(MemoryManager(self.project_dir).spec()["project_name"], "Chorus Project")

        wf = WorkflowState(self.project_dir)
        self.assertEqual(wf.phase, Phase.DESIGN)
        self.assertEqual(wf.status, Status.AWAITING_APPROVAL)

    def test_workflow_service_updates_phase_and_branch_metadata(self) -> None:
        self._write_spec()

        service = backend.get_workflow_service()
        wf = service.initialize(self.project_dir, Phase.PLANNING)
        wf = service.set_branches(
            self.project_dir,
            base_branch="worktree/chorus-project",
            current_branch="worktree/chorus-project",
        )

        self.assertEqual(wf.phase, Phase.PLANNING)
        self.assertEqual(wf.status, Status.IN_PROGRESS)
        self.assertEqual(wf._data["current_branch"], "worktree/chorus-project")

        record = self.store.get_project(project_slug(self.project_dir))
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.phase, Phase.PLANNING.value)
        self.assertEqual(record.current_branch, "worktree/chorus-project")

    def test_workflow_service_handles_blocked_round_trip(self) -> None:
        service = backend.get_workflow_service()

        wf = service.set_value(self.project_dir, "blocked", blocked_reason="manual hold")
        self.assertEqual(wf.status, Status.BLOCKED)
        self.assertEqual(wf.blocked_reason, "manual hold")

        wf = service.set_value(self.project_dir, Status.IN_PROGRESS.value)
        self.assertEqual(wf.status, Status.IN_PROGRESS)
        self.assertIsNone(wf.blocked_reason)

    def test_store_persists_project_source_and_repo_binding(self) -> None:
        self._write_spec()
        self.store.sync_project(
            self.project_dir,
            workflow_data={"phase": "requirement", "status": "in_progress"},
        )
        source = self.store.upsert_project_source(
            project_slug=project_slug(self.project_dir),
            source_type="prd_file",
            label="Checkout PRD",
            location="/tmp/prd.md",
            content_text="# PRD",
            metadata={"format": "markdown"},
        )
        repo_binding = self.store.upsert_project_repo_binding(
            project_slug=project_slug(self.project_dir),
            provider="github",
            mode="existing",
            repo_name="owner/repo",
            repo_url="https://github.com/owner/repo",
            local_path=str(self.project_dir),
            metadata={"selected_from": "picker"},
        )

        self.assertEqual(source.source_type, "prd_file")
        self.assertEqual(self.store.get_project_source(project_slug(self.project_dir)).location, "/tmp/prd.md")
        self.assertEqual(repo_binding.repo_name, "owner/repo")
        self.assertEqual(self.store.get_project_repo_binding(project_slug(self.project_dir)).provider, "github")

    def test_spawn_for_project_creates_job_records_and_workflow_events(self) -> None:
        self._write_spec()
        self.store.sync_project(
            self.project_dir,
            workflow_data={"phase": "implementation", "status": "in_progress"},
        )

        finished = threading.Event()

        def handler(event) -> None:
            if event.type == EventType.JOB_FINISHED:
                finished.set()

        self.runtime.subscribe(handler)
        with mock.patch(
            "sdlc_orchestrator.backend._build_executor_command",
            return_value=[sys.executable, "-c", "print('job run ok')"],
        ):
            run = self.runtime.spawn_for_project(
                self.project_dir,
                job_type="implementation",
                preferred_agent="codex",
                trigger="unit_test_job",
                allow_fallback=False,
                payload={"story_id": "STORY-001"},
            )

        self.assertIsNotNone(run)
        self.assertTrue(finished.wait(timeout=5), "job did not finish")
        assert run is not None

        job = self.store.recent_jobs(project_slug(self.project_dir), limit=1)[0]
        self.assertEqual(job.job_type, "implementation")
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.run_id, run.id)
        self.assertEqual(job.payload["story_id"], "STORY-001")

        agent_run = self.store.get_agent_run(run.id)
        self.assertIsNotNone(agent_run)
        assert agent_run is not None
        self.assertEqual(agent_run.job_id, job.id)

        event_types = {
            event.event_type
            for event in self.store.recent_workflow_events(project_slug(self.project_dir), limit=8)
        }
        self.assertIn(EventType.JOB_QUEUED.value, event_types)
        self.assertIn(EventType.JOB_STARTED.value, event_types)
        self.assertIn(EventType.AGENT_RUN_STARTED.value, event_types)
        self.assertIn(EventType.AGENT_RUN_FINISHED.value, event_types)
        self.assertIn(EventType.JOB_FINISHED.value, event_types)

    def test_approval_event_queues_phase_job_with_default_handlers(self) -> None:
        self._write_spec()
        runtime = backend.get_runtime()
        runtime.sync_project(
            self.project_dir,
            workflow_data={"phase": "design", "status": "awaiting_approval"},
        )

        finished = threading.Event()

        def handler(event) -> None:
            if event.type == EventType.JOB_FINISHED:
                finished.set()

        runtime.subscribe(handler)
        with mock.patch(
            "sdlc_orchestrator.backend._build_executor_command",
            return_value=[sys.executable, "-c", "print('approval followup ok')"],
        ):
            runtime.record_approval(
                self.project_dir,
                phase="design",
                source="github_webhook",
                state="approved",
                payload={"pr_number": 42},
            )

        self.assertTrue(finished.wait(timeout=5), "approval-triggered job did not finish")

        job = self.store.recent_jobs(project_slug(self.project_dir), limit=1)[0]
        self.assertEqual(job.job_type, "design")
        self.assertEqual(job.skill, "sdlc-design")
        self.assertEqual(job.status, "succeeded")
        self.assertEqual(job.payload["approval_state"], "approved")

        event_types = [
            event.event_type
            for event in self.store.recent_workflow_events(project_slug(self.project_dir), limit=10)
        ]
        self.assertIn(EventType.APPROVAL_RECEIVED.value, event_types)
        self.assertIn(EventType.JOB_QUEUED.value, event_types)
        self.assertIn(EventType.JOB_STARTED.value, event_types)
        self.assertIn(EventType.JOB_FINISHED.value, event_types)

    def test_job_type_resolves_phase_specific_skill(self) -> None:
        self._write_spec()
        self.store.sync_project(
            self.project_dir,
            workflow_data={"phase": "planning", "status": "in_progress"},
        )
        finished = threading.Event()

        def handler(event) -> None:
            if event.type == EventType.JOB_FINISHED:
                finished.set()

        self.runtime.subscribe(handler)

        with mock.patch(
            "sdlc_orchestrator.backend._build_executor_command",
            return_value=[sys.executable, "-c", "print('planning ok')"],
        ) as build_command:
            run = self.runtime.spawn_for_project(
                self.project_dir,
                job_type="planning",
                trigger="unit_test_planning",
                allow_fallback=False,
            )

        self.assertIsNotNone(run)
        self.assertTrue(finished.wait(timeout=5), "planning job did not finish")
        assert run is not None
        self.assertEqual(build_command.call_args[0][1], "sdlc-plan")

        job = self.store.get_job(run.job_id)
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.skill, "sdlc-plan")
        self.assertEqual(job.metadata["dispatched_skill"], "sdlc-plan")
        self.assertIsNone(job.metadata["explicit_skill"])

    def test_job_type_can_use_project_job_agent_override(self) -> None:
        self._write_spec_with_job_agents()
        self.store.sync_project(
            self.project_dir,
            workflow_data={"phase": "planning", "status": "in_progress"},
        )
        finished = threading.Event()

        def handler(event) -> None:
            if event.type == EventType.JOB_FINISHED:
                finished.set()

        self.runtime.subscribe(handler)

        with mock.patch(
            "sdlc_orchestrator.backend._build_executor_command",
            return_value=[sys.executable, "-c", "print('planning agent override ok')"],
        ) as build_command:
            run = self.runtime.spawn_for_project(
                self.project_dir,
                job_type="planning",
                trigger="unit_test_job_agents",
                allow_fallback=False,
            )

        self.assertIsNotNone(run)
        self.assertTrue(finished.wait(timeout=5), "planning override job did not finish")
        assert run is not None
        self.assertEqual(build_command.call_args[0][0], "kiro")

        job = self.store.get_job(run.job_id)
        self.assertIsNotNone(job)
        assert job is not None
        self.assertEqual(job.requested_agent, "kiro")


if __name__ == "__main__":
    unittest.main()

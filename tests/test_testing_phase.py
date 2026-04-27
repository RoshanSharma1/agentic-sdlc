from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sdlc_orchestrator.state_machine import Phase, Status, WorkflowState
from sdlc_orchestrator.utils import project_slug


class TestingPhaseWorkflowTests(unittest.TestCase):
    def _make_workflow(self) -> WorkflowState:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        project_dir = Path(tempdir.name)
        return WorkflowState(project_dir)

    def _write_legacy_state(self, payload: dict) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        project_dir = Path(tempdir.name)
        state_path = project_dir / ".sdlc" / "workflow" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(payload))
        return project_dir

    def test_defaults_include_testing_phase_and_artifacts(self) -> None:
        wf = self._make_workflow()

        self.assertIn(Phase.TESTING.value, wf._data["phases"])
        self.assertIn("test_spec", wf.artifacts)
        self.assertIn("test_cases", wf.artifacts)
        self.assertIn("test_results", wf.artifacts)

    def test_finish_implementation_advances_to_testing(self) -> None:
        wf = self._make_workflow()
        wf._data["phase"] = Phase.IMPLEMENTATION.value
        wf._data["status"] = Status.IN_PROGRESS.value
        wf._data["phases"][Phase.IMPLEMENTATION.value]["status"] = Status.IN_PROGRESS.value
        wf.save()

        wf.finish_implementation()

        self.assertEqual(wf.phase, Phase.TESTING)
        self.assertEqual(wf.status, Status.IN_PROGRESS)
        self.assertEqual(
            wf._data["phases"][Phase.IMPLEMENTATION.value]["status"],
            Status.DONE.value,
        )
        self.assertEqual(
            wf._data["phases"][Phase.TESTING.value]["status"],
            Status.IN_PROGRESS.value,
        )

    def test_approve_testing_advances_to_documentation(self) -> None:
        wf = self._make_workflow()
        wf._data["phase"] = Phase.TESTING.value
        wf._data["status"] = Status.AWAITING_APPROVAL.value
        wf._data["phases"][Phase.TESTING.value]["status"] = Status.AWAITING_APPROVAL.value
        wf.save()

        wf.approve()

        self.assertEqual(wf.phase, Phase.DOCUMENTATION)
        self.assertEqual(wf.status, Status.IN_PROGRESS)
        self.assertEqual(
            wf._data["phases"][Phase.TESTING.value]["status"],
            Status.DONE.value,
        )

    def test_migration_backfills_testing_for_completed_legacy_state(self) -> None:
        project_dir = self._write_legacy_state(
            {
                "phase": Phase.DONE.value,
                "status": Status.DONE.value,
                "phases": {
                    "requirement": {"status": Status.DONE.value, "stories": {}},
                    "design": {"status": Status.DONE.value, "stories": {}},
                    "planning": {"status": Status.DONE.value, "stories": {}},
                    "implementation": {"status": Status.DONE.value, "stories": {}},
                    "documentation": {"status": Status.DONE.value, "stories": {}},
                },
                "artifacts": {
                    "requirements": "docs/requirements.md",
                },
            }
        )

        wf = WorkflowState(project_dir)

        self.assertEqual(wf._data["phases"][Phase.TESTING.value]["status"], Status.DONE.value)
        self.assertIn("test_spec", wf.artifacts)
        self.assertIn("test_cases", wf.artifacts)
        self.assertIn("test_results", wf.artifacts)

    def test_migration_syncs_current_phase_status_for_legacy_state(self) -> None:
        project_dir = self._write_legacy_state(
            {
                "phase": Phase.REQUIREMENT.value,
                "status": Status.IN_PROGRESS.value,
                "phases": {
                    "requirement": {"status": Status.PENDING.value, "stories": {}},
                    "design": {"status": Status.PENDING.value, "stories": {}},
                    "planning": {"status": Status.PENDING.value, "stories": {}},
                    "implementation": {"status": Status.PENDING.value, "stories": {}},
                    "documentation": {"status": Status.PENDING.value, "stories": {}},
                },
                "artifacts": {},
            }
        )

        wf = WorkflowState(project_dir)

        self.assertEqual(
            wf._data["phases"][Phase.REQUIREMENT.value]["status"],
            Status.IN_PROGRESS.value,
        )
        self.assertEqual(
            wf._data["phases"][Phase.TESTING.value]["status"],
            Status.PENDING.value,
        )

    def test_testing_artifacts_are_namespaced_by_project(self) -> None:
        project_dir = self._write_legacy_state(
            {
                "phase": Phase.TESTING.value,
                "status": Status.IN_PROGRESS.value,
                "phases": {
                    "requirement": {"status": Status.DONE.value, "stories": {}},
                    "design": {"status": Status.DONE.value, "stories": {}},
                    "planning": {"status": Status.DONE.value, "stories": {}},
                    "implementation": {"status": Status.DONE.value, "stories": {}},
                    "testing": {"status": Status.IN_PROGRESS.value, "stories": {}},
                    "documentation": {"status": Status.PENDING.value, "stories": {}},
                },
                "artifacts": {},
            }
        )
        wf = WorkflowState(project_dir)

        wf.mark_artifact("test_spec", "docs/sdlc/test-spec.md")
        wf.mark_artifact("test_cases", "test-cases.md")
        wf.mark_artifact("test_results", "test-results.md")

        slug = project_slug(project_dir)
        self.assertEqual(
            wf.artifacts["test_spec"],
            f"docs/sdlc/{slug}/test-spec.md",
        )
        self.assertEqual(
            wf.artifacts["test_cases"],
            f"docs/sdlc/{slug}/test-cases.md",
        )
        self.assertEqual(
            wf.artifacts["test_results"],
            f"docs/sdlc/{slug}/test-results.md",
        )

    def test_existing_testing_artifacts_are_normalized_per_project_on_load(self) -> None:
        project_dir = self._write_legacy_state(
            {
                "phase": Phase.TESTING.value,
                "status": Status.IN_PROGRESS.value,
                "phases": {
                    "requirement": {"status": Status.DONE.value, "stories": {}},
                    "design": {"status": Status.DONE.value, "stories": {}},
                    "planning": {"status": Status.DONE.value, "stories": {}},
                    "implementation": {"status": Status.DONE.value, "stories": {}},
                    "testing": {"status": Status.IN_PROGRESS.value, "stories": {}},
                    "documentation": {"status": Status.PENDING.value, "stories": {}},
                },
                "artifacts": {
                    "test_results": "docs/sdlc/test-results.md",
                },
            }
        )

        wf = WorkflowState(project_dir)
        slug = project_slug(project_dir)

        self.assertEqual(
            wf.artifacts["test_results"],
            f"docs/sdlc/{slug}/test-results.md",
        )


if __name__ == "__main__":
    unittest.main()

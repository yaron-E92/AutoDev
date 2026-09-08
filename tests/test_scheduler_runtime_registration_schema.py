from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import role_runtime, scheduler_registration
from automation.scheduler_types import SchedulerRegistration


class SchedulerRuntimeRegistrationSchemaTests(unittest.TestCase):
    def _legacy_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "github_repository": "owner/repo",
            "source_repository": "/source",
            "worker_repository": "/worker",
            "default_branch": "main",
            "backend": "cron",
            "cadence_minutes": 15,
            "launcher": "/usr/bin/autodev",
            "task_id": "autodev-owner-repo",
            "installed_at": "2026-09-07T00:00:00Z",
            "last_run": None,
        }

    def test_schema_one_registration_still_loads_unpinned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "registration.json"
            path.write_text(json.dumps(self._legacy_payload()), encoding="utf-8")
            loaded = scheduler_registration._load_registration(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.role_runtime, "")
            self.assertIsNone(loaded.runtime_state)

    def test_schema_two_runtime_state_round_trips_and_validates_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "registration.json"
            state = role_runtime.SchedulerRuntimeState(
                name="opencode",
                source="explicit-scheduler",
                identity={"runtime": "opencode", "version": "1.18.21"},
                environment={"PATH": "/runtime/bin"},
                routes={"reader": "ollama/gpt-oss:20b-autodev"},
            )
            registration = SchedulerRegistration(
                github_repository="owner/repo",
                source_repository="/source",
                worker_repository="/worker",
                default_branch="main",
                backend="cron",
                cadence_minutes=15,
                launcher="/usr/bin/autodev",
                task_id="autodev-owner-repo",
                installed_at="2026-09-07T00:00:00Z",
                role_runtime="opencode",
                runtime_state=state.to_json(),
            )
            scheduler_registration._write_registration(path, registration)
            loaded = scheduler_registration._load_registration(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.role_runtime, "opencode")
            self.assertEqual(
                loaded.runtime_state["fingerprint"],  # type: ignore[index]
                state.fingerprint,
            )

    def test_last_run_style_rewrite_preserves_runtime_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "registration.json"
            state = role_runtime.SchedulerRuntimeState(
                name="opencode",
                source="explicit-scheduler",
                identity={"runtime": "opencode", "version": "1.18.21"},
                environment={"PATH": "/runtime/bin"},
                routes={"reader": "ollama/gpt-oss:20b-autodev"},
            )
            registered = SchedulerRegistration(
                github_repository="owner/repo",
                source_repository="/source",
                worker_repository="/worker",
                default_branch="main",
                backend="cron",
                cadence_minutes=15,
                launcher="/usr/bin/autodev",
                task_id="autodev-owner-repo",
                installed_at="2026-09-07T00:00:00Z",
                role_runtime="opencode",
                runtime_state=state.to_json(),
            )
            scheduler_registration._write_registration(path, registered)

            reconstructed = SchedulerRegistration(
                github_repository=registered.github_repository,
                source_repository=registered.source_repository,
                worker_repository=registered.worker_repository,
                default_branch=registered.default_branch,
                backend=registered.backend,
                cadence_minutes=registered.cadence_minutes,
                launcher=registered.launcher,
                task_id=registered.task_id,
                installed_at=registered.installed_at,
                last_run={"state": "NO_READY_WORK"},
            )
            scheduler_registration._write_registration(path, reconstructed)
            loaded = scheduler_registration._load_registration(path)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.role_runtime, "opencode")
            self.assertEqual(
                loaded.runtime_state["fingerprint"],  # type: ignore[index]
                state.fingerprint,
            )
            self.assertEqual(loaded.last_run, {"state": "NO_READY_WORK"})


if __name__ == "__main__":
    unittest.main()

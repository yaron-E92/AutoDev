from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import queue_selection, scheduler
from automation.scheduler_types import SchedulerRegistration


class SchedulerRuntimeDispatchTests(unittest.TestCase):
    def _registration(self, root: Path) -> tuple[Path, SchedulerRegistration]:
        worker = root / "worker"
        worker.mkdir(parents=True)
        registration = SchedulerRegistration(
            github_repository="owner/repo",
            source_repository=str(root / "source"),
            worker_repository=str(worker),
            default_branch="main",
            backend="cron",
            cadence_minutes=15,
            launcher="/usr/bin/autodev",
            task_id="autodev-owner-repo",
            installed_at="2026-09-01T00:00:00Z",
        )
        path = root / "registration.json"
        path.write_text(
            json.dumps(registration.to_json()),
            encoding="utf-8",
        )
        return path, registration

    def test_nonzero_prepared_coordinator_is_health_blocked_not_dispatched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, _ = self._registration(root)
            stdout = io.StringIO()
            stderr = io.StringIO()
            selection = queue_selection.SelectionResult(
                state="SELECTED",
                repository="owner/repo",
                issue_number=71,
                explanation="selected",
            )

            with (
                mock.patch(
                    "automation.scheduler._prepare_worker",
                    return_value=queue_selection.ExistingRun("NONE"),
                ),
                mock.patch(
                    "automation.scheduler.queue_selection.select_next",
                    return_value=selection,
                ),
                mock.patch(
                    "automation.scheduler._coordinator_state",
                    return_value="Prepared",
                ),
            ):
                code = scheduler.run_once(
                    registration_file,
                    coordinator=lambda argv: 1,
                    stdout=stdout,
                    stderr=stderr,
                    claiming_enabled=False,
                )

            self.assertEqual(code, 1)
            self.assertEqual(stdout.getvalue(), "")
            payload = json.loads(stderr.getvalue())
            self.assertEqual(payload["state"], "RUN_HEALTH_BLOCKED")
            self.assertEqual(payload["coordinator_state"], "Prepared")
            self.assertEqual(payload["coordinator_exit_code"], 1)

    def test_successful_prepared_state_keeps_existing_dispatched_semantics(self) -> None:
        self.assertEqual(
            scheduler._dispatch_state("Prepared", coordinator_exit_code=0),
            "DISPATCHED",
        )

    def test_existing_durable_run_still_refreshes_runtime_before_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _registration_file, registration = self._registration(root)
            worker = Path(registration.worker_repository)
            (worker / ".git").mkdir()
            runtime = SimpleNamespace(name="fake")

            with (
                mock.patch("automation.scheduler._git"),
                mock.patch(
                    "automation.scheduler.queue_selection.inspect_existing_run",
                    return_value=queue_selection.ExistingRun("RESUME_EXISTING"),
                ),
                mock.patch(
                    "automation.scheduler.role_runtime.select_runtime",
                    return_value=(runtime, "test"),
                ),
                mock.patch(
                    "automation.scheduler.role_runtime.prepare_scheduler_worker"
                ) as prepare_runtime,
            ):
                existing = scheduler._prepare_worker(
                    registration,
                    runner=lambda *args, **kwargs: None,
                )

            self.assertEqual(existing.state, "RESUME_EXISTING")
            prepare_runtime.assert_called_once_with(
                runtime,
                worker.resolve(),
                runner=mock.ANY,
            )


if __name__ == "__main__":
    unittest.main()

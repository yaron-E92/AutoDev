from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import queue_selection, scheduler


class CronRunner:
    def __init__(self, initial: str = ""):
        self.crontab = initial
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        args = list(argv)
        self.calls.append(args)
        if args == ["crontab", "-l"]:
            if self.crontab:
                return SimpleNamespace(returncode=0, stdout=self.crontab, stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="no crontab for user")
        if args == ["crontab", "-"]:
            self.crontab = str(kwargs.get("input", ""))
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {args}")


class RecordingRunner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        args = list(argv)
        self.calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def make_registration(root: Path, *, backend: str = scheduler.BACKEND_CRON) -> tuple[Path, scheduler.SchedulerRegistration]:
    root = root.expanduser().resolve()
    worker = root / "worker checkout"
    worker.mkdir(parents=True)
    (worker / ".git").mkdir()
    registration_file = root / "scheduler state" / scheduler.REGISTRATION_FILE
    registration = scheduler.SchedulerRegistration(
        github_repository="owner/repo",
        source_repository=str(root / "interactive checkout"),
        worker_repository=str(worker),
        default_branch="main",
        backend=backend,
        cadence_minutes=15,
        launcher=str(root / "bin with spaces" / "autodev"),
        task_id="autodev-owner-repo",
        installed_at="2026-08-23T00:00:00Z",
    )
    scheduler._write_registration(registration_file, registration)
    return registration_file, registration


class SchedulerBackendTests(unittest.TestCase):
    def test_worker_and_registration_are_user_local_not_interactive_checkout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir).resolve()
            interactive = home / "projects" / "repo"

            worker = scheduler.worker_path("owner/repo", home=home)
            registration = scheduler.registration_path("owner/repo", home=home)

            self.assertEqual(worker, home / ".autodev" / "workers" / "owner" / "repo")
            self.assertEqual(
                registration,
                home / ".autodev" / "schedulers" / "owner" / "repo" / "registration.json",
            )
            self.assertNotEqual(worker, interactive)

    def test_cron_install_is_idempotent_and_preserves_unrelated_jobs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, registration = make_registration(root)
            fake = CronRunner("5 1 * * * echo unrelated\n")

            with patch.dict(os.environ, {"PATH": "/usr/local/bin:/usr/bin"}, clear=False):
                scheduler._install_cron(registration, registration_file, runner=fake)
                scheduler._install_cron(registration, registration_file, runner=fake)

            begin, end = scheduler._cron_markers(registration.task_id)
            self.assertEqual(fake.crontab.count(begin), 1)
            self.assertEqual(fake.crontab.count(end), 1)
            self.assertIn("echo unrelated", fake.crontab)
            self.assertIn("scheduler run-once --registration", fake.crontab)
            self.assertIn("scheduler state", fake.crontab)
            self.assertNotIn("queue next", fake.crontab)
            self.assertNotIn("coordinate", fake.crontab)

    def test_cron_uninstall_removes_only_autodev_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, registration = make_registration(root)
            fake = CronRunner("5 1 * * * echo unrelated\n")
            scheduler._install_cron(registration, registration_file, runner=fake)

            scheduler._uninstall_backend(registration, home=root, runner=fake)

            self.assertIn("echo unrelated", fake.crontab)
            self.assertNotIn("AutoDev scheduler", fake.crontab)

    def test_systemd_user_units_only_wake_shared_dispatcher_and_are_persistent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, registration = make_registration(
                root,
                backend=scheduler.BACKEND_SYSTEMD,
            )
            fake = RecordingRunner()

            with patch.dict(os.environ, {"PATH": "/usr/local/bin:/usr/bin"}, clear=False):
                scheduler._install_systemd(
                    registration,
                    registration_file,
                    home=root,
                    runner=fake,
                )

            service, timer = scheduler._systemd_paths(registration, home=root)
            service_text = service.read_text(encoding="utf-8")
            timer_text = timer.read_text(encoding="utf-8")
            self.assertIn("scheduler", service_text)
            self.assertIn("run-once", service_text)
            self.assertIn("--registration", service_text)
            self.assertNotIn("queue next", service_text)
            self.assertNotIn("coordinate", service_text)
            self.assertIn("Persistent=true", timer_text)
            self.assertIn("OnUnitActiveSec=15min", timer_text)
            self.assertEqual(
                fake.calls[-1],
                ["systemctl", "--user", "enable", "--now", f"{registration.task_id}.timer"],
            )

    def test_windows_task_action_preserves_spaces_and_only_wakes_dispatcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, registration = make_registration(
                root,
                backend=scheduler.BACKEND_WINDOWS,
            )
            fake = RecordingRunner()

            scheduler._install_windows_task(registration, registration_file, runner=fake)

            command = fake.calls[-1]
            self.assertEqual(command[:3], ["schtasks", "/Create", "/TN"])
            action = command[command.index("/TR") + 1]
            self.assertIn("scheduler", action)
            self.assertIn("run-once", action)
            self.assertIn("registration.json", action)
            self.assertIn("cmd.exe", action)
            self.assertNotIn("queue next", action)
            self.assertNotIn("coordinate", action)


class SchedulerDispatchTests(unittest.TestCase):
    def test_no_ready_work_exits_without_coordinator_or_model_dispatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, _registration = make_registration(root)
            output = io.StringIO()
            coordinator_calls: list[list[str]] = []

            def coordinator(argv: list[str]) -> int:
                coordinator_calls.append(list(argv))
                return 0

            selection = queue_selection.SelectionResult(
                state="NO_READY_WORK",
                repository="owner/repo",
                explanation="nothing eligible",
            )
            with patch.object(scheduler, "_prepare_worker"), patch.object(
                scheduler.queue_selection,
                "select_next",
                return_value=selection,
            ):
                code = scheduler.run_once(
                    registration_file,
                    coordinator=coordinator,
                    stdout=output,
                )

            self.assertEqual(code, 0)
            self.assertEqual(coordinator_calls, [])
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["state"], "NO_READY_WORK")

    def test_existing_resumable_run_is_dispatched_with_resume_before_new_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, registration = make_registration(root)
            output = io.StringIO()
            calls: list[tuple[list[str], str, str]] = []

            def coordinator(argv: list[str]) -> int:
                calls.append(
                    (
                        list(argv),
                        os.environ.get("AUTODEV_HEADLESS", ""),
                        os.environ.get("AUTODEV_INTERACTIVE_CONSENT", ""),
                    )
                )
                return 0

            selection = queue_selection.SelectionResult(
                state="RESUME_EXISTING",
                repository="owner/repo",
                issue_number=42,
                source="existing-run",
                explanation="resume durable run first",
            )
            with patch.dict(
                os.environ,
                {"AUTODEV_INTERACTIVE_CONSENT": "controlling-terminal"},
                clear=False,
            ), patch.object(scheduler, "_prepare_worker"), patch.object(
                scheduler.queue_selection,
                "select_next",
                return_value=selection,
            ), patch.object(scheduler, "_coordinator_state", return_value="PR_READY"):
                code = scheduler.run_once(
                    registration_file,
                    coordinator=coordinator,
                    stdout=output,
                )

            self.assertEqual(code, 0)
            self.assertEqual(len(calls), 1)
            argv, headless, interactive = calls[0]
            self.assertEqual(
                argv,
                ["coordinate", "--repo", registration.worker_repository, "--resume"],
            )
            self.assertEqual(headless, "1")
            self.assertEqual(interactive, "")
            self.assertEqual(os.environ.get("AUTODEV_INTERACTIVE_CONSENT"), None)

    def test_selected_issue_uses_existing_coordinator_and_preserves_attention_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, registration = make_registration(root)
            output = io.StringIO()
            calls: list[list[str]] = []

            def coordinator(argv: list[str]) -> int:
                calls.append(list(argv))
                return 0

            selection = queue_selection.SelectionResult(
                state="SELECTED",
                repository="owner/repo",
                issue_number=162,
                issue_title="Manual prerequisite fixture",
                explanation="oldest eligible",
            )
            with patch.object(scheduler, "_prepare_worker"), patch.object(
                scheduler.queue_selection,
                "select_next",
                return_value=selection,
            ), patch.object(
                scheduler,
                "_coordinator_state",
                return_value="AttentionRequired",
            ):
                code = scheduler.run_once(
                    registration_file,
                    coordinator=coordinator,
                    stdout=output,
                )

            self.assertEqual(code, 0)
            self.assertEqual(
                calls,
                [[
                    "coordinate",
                    "--repo",
                    registration.worker_repository,
                    "--arguments",
                    "162",
                ]],
            )
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["state"], "ATTENTION_REQUIRED")
            self.assertEqual(payload["coordinator_state"], "AttentionRequired")

    def test_existing_attention_run_is_successful_non_runnable_without_coordinator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, _registration = make_registration(root)
            output = io.StringIO()
            selection = queue_selection.SelectionResult(
                state="ATTENTION_REQUIRED",
                repository="owner/repo",
                issue_number=176,
                source="existing-run",
                explanation="external publisher identity required",
            )

            with patch.object(scheduler, "_prepare_worker"), patch.object(
                scheduler.queue_selection,
                "select_next",
                return_value=selection,
            ) as select_next, patch.object(
                scheduler.opencode_entrypoint,
                "run",
            ) as coordinator:
                code = scheduler.run_once(registration_file, stdout=output)

            self.assertEqual(code, 0)
            select_next.assert_called_once()
            coordinator.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["state"], "ATTENTION_REQUIRED")

    def test_dirty_worker_without_durable_run_fails_without_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _registration_file, registration = make_registration(root)
            git_calls: list[list[str]] = []

            def fake_git(_repo, args, **_kwargs):
                git_calls.append(list(args))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(scheduler, "_git", side_effect=fake_git), patch.object(
                scheduler,
                "_git_status",
                return_value=" M user-file.txt",
            ), patch.object(
                scheduler.queue_selection,
                "inspect_existing_run",
                return_value=queue_selection.ExistingRun("NONE"),
            ):
                with self.assertRaisesRegex(scheduler.SchedulerError, "unexpected local changes"):
                    scheduler._prepare_worker(registration, runner=RecordingRunner())

            flat = " ".join(" ".join(call) for call in git_calls)
            self.assertNotIn("reset", flat)
            self.assertNotIn("clean", flat)

    def test_overlap_is_suppressed_before_worker_or_coordinator_activity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration_file, _registration = make_registration(root)
            output = io.StringIO()

            class BusyLock:
                acquired = False

                def __init__(self, _path):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

            with patch.object(scheduler, "SchedulerLock", BusyLock), patch.object(
                scheduler,
                "_prepare_worker",
            ) as prepare, patch.object(
                scheduler.opencode_entrypoint,
                "run",
            ) as coordinator:
                code = scheduler.run_once(registration_file, stdout=output)

            self.assertEqual(code, 0)
            prepare.assert_not_called()
            coordinator.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["state"], "OVERLAP_SUPPRESSED")


if __name__ == "__main__":
    unittest.main()

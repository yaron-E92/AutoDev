from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from automation import (
    notification_cli,
    notification_delivery,
    notification_events,
    notification_outcomes,
    notification_storage,
    scheduler_health_contract,
    scheduler_health_notifications,
    scheduler_health_storage,
    scheduler_types,
    workflow_stages,
)
from automation.notification_contract import (
    EVENT_BLOCKED,
    EVENT_FAILED,
    EVENT_READY_FOR_REVIEW,
    MODE_MANUAL,
    MODE_SCHEDULED,
    NOTIFICATION_NATIVE,
    NotificationEvent,
    NotificationPolicy,
    NotificationResult,
)


NOW = datetime(2026, 8, 27, 8, 30, tzinfo=timezone.utc)


class SharedNotificationContractTests(unittest.TestCase):
    @staticmethod
    def _repo(root: Path, github_repository: str = "owner/repo") -> Path:
        repo = root / "repo"
        (repo / ".git").mkdir(parents=True)
        config = repo / ".autodev" / "repo.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps({"github_repository": github_repository}),
            encoding="utf-8",
        )
        return repo

    def test_shared_policy_path_is_the_existing_scheduler_notification_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            registration = scheduler_types.registration_path("owner/repo", home=home)
            legacy_path = scheduler_health_storage.notification_path(registration)
            shared_path = notification_storage.policy_path("owner/repo", home=home)

        self.assertEqual(legacy_path, shared_path)

    def test_existing_scheduler_policy_is_visible_to_shared_cli_without_scheduler_install(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            repo = self._repo(root)
            registration = scheduler_types.registration_path("owner/repo", home=home)
            scheduler_health_storage.save_notification_policy(
                registration,
                NotificationPolicy(
                    backend=NOTIFICATION_NATIVE,
                    reminder_hours=24,
                ),
            )
            output = io.StringIO()
            error = io.StringIO()

            code = notification_cli.run_cli(
                ["status", "--repo", str(repo), "--json"],
                home=home,
                stdout=output,
                stderr=error,
            )

        self.assertEqual(code, 0)
        self.assertEqual(error.getvalue(), "")
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["repository"], "owner/repo")
        self.assertEqual(payload["policy"]["backend"], "native")
        self.assertEqual(payload["policy"]["reminder_hours"], 24)

    def test_manual_terminal_payloads_map_to_bounded_safe_events(self):
        secret = "super-secret-model-output"
        base = {
            "issue_number": 205,
            "stage": "semantic",
            "reason": secret,
            "failure_classification": "non-retryable-deterministic",
            "verified_source_identity": "source-id",
        }

        ready = notification_outcomes.event_from_run_payload(
            "owner/repo",
            {**base, "state": "PR_READY", "pr_url": "https://github.com/owner/repo/pull/9"},
        )
        blocked = notification_outcomes.event_from_run_payload(
            "owner/repo",
            {**base, "state": "ATTENTION_REQUIRED"},
        )
        failed = notification_outcomes.event_from_run_payload(
            "owner/repo",
            {**base, "state": "FAILED"},
        )

        self.assertEqual(ready.event, EVENT_READY_FOR_REVIEW)
        self.assertEqual(blocked.event, EVENT_BLOCKED)
        self.assertEqual(failed.event, EVENT_FAILED)
        for event in (ready, blocked, failed):
            title, message = notification_events.render_event(event)
            combined = title + " " + message
            self.assertLessEqual(len(title), notification_events.MAX_TITLE_CHARS)
            self.assertLessEqual(len(message), notification_events.MAX_MESSAGE_CHARS)
            self.assertNotIn(secret, combined)

    def test_unchanged_manual_outcome_notifies_once_then_changed_lineage_notifies_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "events.json"
            policy = NotificationPolicy(backend=NOTIFICATION_NATIVE)
            calls: list[str] = []

            def notifier(_title: str, message: str) -> NotificationResult:
                calls.append(message)
                return NotificationResult(True, True, NOTIFICATION_NATIVE)

            first = notification_outcomes.event_from_run_payload(
                "owner/repo",
                {
                    "state": "BLOCKED",
                    "issue_number": 205,
                    "failed_stage": "local-check",
                    "failure_classification": "setup/configuration",
                    "failure_fingerprint": "same",
                },
            )
            changed = notification_outcomes.event_from_run_payload(
                "owner/repo",
                {
                    "state": "BLOCKED",
                    "issue_number": 205,
                    "failed_stage": "local-check",
                    "failure_classification": "code-repairable",
                    "failure_fingerprint": "different",
                },
            )
            notification_events.observe_event(
                state_path,
                first,
                policy=policy,
                notifier=notifier,
                now=NOW,
            )
            notification_events.observe_event(
                state_path,
                first,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(minutes=1),
            )
            notification_events.observe_event(
                state_path,
                changed,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(minutes=2),
            )

        self.assertEqual(len(calls), 2)

    def test_failed_delivery_is_recorded_and_unchanged_event_is_suppressed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "events.json"
            policy = NotificationPolicy(backend=NOTIFICATION_NATIVE)
            calls = 0
            event = NotificationEvent(
                repository="owner/repo",
                mode=MODE_MANUAL,
                event=EVENT_FAILED,
                fingerprint="failure-one",
                observed_at="2026-08-27T08:30:00Z",
                issue_number=205,
                summary="Issue #205 failed.",
            )

            def notifier(_title: str, _message: str) -> NotificationResult:
                nonlocal calls
                calls += 1
                return NotificationResult(
                    True,
                    False,
                    NOTIFICATION_NATIVE,
                    "desktop unavailable",
                )

            notification_events.observe_event(
                state_path,
                event,
                policy=policy,
                notifier=notifier,
                now=NOW,
            )
            second = notification_events.observe_event(
                state_path,
                event,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(minutes=5),
            )
            state = notification_storage.load_event_state_path(state_path)

        self.assertEqual(calls, 1)
        self.assertFalse(second.attempted)
        manual = state["modes"][MODE_MANUAL]
        self.assertFalse(manual["last_notification"]["delivered"])

    def test_native_delivery_uses_linux_and_windows_local_backends(self):
        calls: list[list[str]] = []

        def runner(argv, **_kwargs):
            calls.append(list(argv))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        linux = notification_delivery.native_notify(
            "AutoDev",
            "Ready",
            runner=runner,
            which=lambda name: "/usr/bin/notify-send" if name == "notify-send" else None,
            platform_name="posix",
        )
        windows = notification_delivery.native_notify(
            "AutoDev",
            "Ready",
            runner=runner,
            which=lambda name: "C:/Windows/System32/msg.exe" if name in {"msg", "msg.exe"} else None,
            platform_name="windows",
        )

        self.assertTrue(linux.delivered)
        self.assertTrue(windows.delivered)
        self.assertEqual(calls[0][0], "/usr/bin/notify-send")
        self.assertEqual(calls[1][:3], ["C:/Windows/System32/msg.exe", "*", "/TIME:10"])

    def test_notification_delivery_exception_cannot_change_run_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            repo = self._repo(root)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / workflow_stages.DIAGNOSTICS_FILE).write_text("{}\n", encoding="utf-8")
            notification_storage.save_policy(
                "owner/repo",
                NotificationPolicy(backend=NOTIFICATION_NATIVE),
                home=home,
            )
            payload = {
                "state": "FAILED",
                "issue_number": 205,
                "failed_stage": "semantic",
                "failure_classification": "non-retryable-deterministic",
            }
            original = dict(payload)

            result = notification_outcomes.best_effort_notify_run_outcome(
                repo,
                payload,
                home=home,
                notifier=lambda _title, _message: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )

        self.assertEqual(payload, original)
        self.assertTrue(result.attempted)
        self.assertFalse(result.delivered)
        self.assertEqual(diagnostics["notification_outcome"]["event"], EVENT_FAILED)
        self.assertFalse(diagnostics["notification_outcome"]["delivered"])

    def test_scheduler_health_uses_same_event_state_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            registration = root / "registration.json"
            registration.write_text("{}\n", encoding="utf-8")
            snapshot = scheduler_health_contract.HealthSnapshot(
                state="PR_READY",
                repository="owner/repo",
                observed_at="2026-08-27T08:30:00Z",
                fingerprint="scheduler-ready",
                queue={},
                unmanaged_open=0,
                issue_number=205,
            )
            policy = NotificationPolicy(backend=NOTIFICATION_NATIVE)
            calls: list[str] = []

            scheduler_health_notifications.observe_health(
                registration,
                snapshot,
                policy=policy,
                notifier=lambda _title, message: (
                    calls.append(message)
                    or NotificationResult(True, True, NOTIFICATION_NATIVE)
                ),
                now=NOW,
            )
            state = notification_storage.load_event_state_path(
                root / notification_storage.EVENT_STATE_FILE
            )

        self.assertEqual(len(calls), 1)
        scheduled = state["modes"][MODE_SCHEDULED]
        self.assertEqual(scheduled["current"]["event"], EVENT_READY_FOR_REVIEW)
        self.assertEqual(scheduled["current"]["issue_number"], 205)


if __name__ == "__main__":
    unittest.main()

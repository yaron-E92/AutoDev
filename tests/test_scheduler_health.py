from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from automation import issue_queue, queue_selection, scheduler, scheduler_health


NOW = datetime(2026, 8, 23, 7, 30, tzinfo=timezone.utc)


def qissue(number: int, *, labels: tuple[str, ...] = (), state: str = "open") -> issue_queue.QueueIssue:
    return issue_queue.QueueIssue(
        number=number,
        title=f"Issue {number}",
        url=f"https://github.test/owner/repo/issues/{number}",
        state=state,
        labels=labels,
        created_at=f"2026-08-{min(number, 28):02d}T00:00:00Z",
    )


def qstate(number: int, reason: str, *, blockers: tuple[int, ...] = ()) -> issue_queue.QueueState:
    labels = [issue_queue.MANAGED_LABEL]
    if reason == "ready":
        labels.append(issue_queue.READY_LABEL)
    if reason == "blocked":
        labels.append(issue_queue.BLOCKED_LABEL)
    if reason == "attention":
        labels.append(issue_queue.ATTENTION_LABEL)
    if reason == "running":
        labels.append(issue_queue.RUNNING_LABEL)
    return issue_queue.QueueState(
        issue=qissue(number, labels=tuple(labels)),
        reason=reason,
        open_blockers=tuple(
            issue_queue.Blocker(
                id=1000 + item,
                number=item,
                title=f"Blocker {item}",
                url=f"https://github.test/owner/repo/issues/{item}",
                state="open",
            )
            for item in blockers
        ),
    )


def compute(
    states: list[issue_queue.QueueState],
    *,
    existing: queue_selection.ExistingRun | None = None,
    privacy_blocked: bool = False,
    grants: dict[str, int] | None = None,
    raw_status: str = "",
    raw_issue: int = 0,
    force_error: bool = False,
    last_outcome: str = "",
) -> scheduler_health.HealthSnapshot:
    all_issues = [state.issue for state in states]
    all_issues.append(qissue(99, labels=()))
    with patch.object(scheduler_health.issue_queue, "inspect_queue", return_value=states), patch.object(
        scheduler_health.issue_queue,
        "list_issues",
        return_value=all_issues,
    ), patch.object(
        scheduler_health.queue_selection,
        "inspect_existing_run",
        return_value=existing or queue_selection.ExistingRun("NONE"),
    ), patch.object(
        scheduler_health,
        "_raw_run_status",
        return_value=(raw_status, raw_issue),
    ):
        return scheduler_health.compute_health(
            Path("."),
            "owner/repo",
            now=NOW,
            force_error=force_error,
            last_outcome=last_outcome,
            privacy_probe=lambda _repo: (
                privacy_blocked,
                grants or {"active": 0, "expired": 0, "revoked": 0},
            ),
        )


class SchedulerHealthComputationTests(unittest.TestCase):
    def test_ready_work_and_empty_queue_are_not_scheduler_failures(self):
        ready = compute([qstate(1, "ready")])
        empty = compute([])

        self.assertEqual(ready.state, "READY_WORK_AVAILABLE")
        self.assertEqual(ready.queue["ready"], 1)
        self.assertEqual(ready.unmanaged_open, 1)
        self.assertEqual(empty.state, "NO_READY_WORK")

    def test_all_managed_blocked_reports_useful_blocker_counts(self):
        snapshot = compute(
            [
                qstate(1, "blocked", blockers=(50, 60)),
                qstate(2, "blocked", blockers=(50,)),
                qstate(3, "blocked", blockers=(50,)),
            ]
        )

        self.assertEqual(snapshot.state, "ALL_MANAGED_WORK_BLOCKED")
        self.assertEqual(snapshot.blocker_counts, {"50": 3, "60": 1})
        self.assertIn("Top blocker #50 blocks 3", scheduler_health.render_health(snapshot))

    def test_resumable_run_is_distinct_from_terminal_failure(self):
        resumable = compute(
            [qstate(42, "running")],
            existing=queue_selection.ExistingRun(
                "RESUME_EXISTING",
                issue_number=42,
                next_stage="semantic",
                next_action="verifier",
            ),
        )
        failed = compute(
            [qstate(42, "running")],
            existing=queue_selection.ExistingRun(
                "RUN_HEALTH_BLOCKED",
                issue_number=42,
            ),
        )

        self.assertEqual(resumable.state, "RUNNING_OR_RESUMABLE")
        self.assertEqual(resumable.next_stage, "semantic")
        self.assertEqual(failed.state, "SCHEDULER_ERROR")

    def test_ready_for_review_is_first_class_health_state(self):
        snapshot = compute([], raw_status="ReadyForReview", raw_issue=42)

        self.assertEqual(snapshot.state, "PR_READY")
        self.assertEqual(snapshot.issue_number, 42)

    def test_expired_or_missing_required_privacy_grant_becomes_attention_before_model_work(self):
        coordinator = unittest.mock.Mock()
        snapshot = compute(
            [qstate(57, "ready")],
            privacy_blocked=True,
            grants={"active": 0, "expired": 1, "revoked": 0},
        )

        self.assertEqual(snapshot.state, "ATTENTION_REQUIRED")
        self.assertEqual(snapshot.attention_kind, "privacy-consent")
        self.assertEqual(snapshot.privacy_grants["expired"], 1)
        self.assertIn("privacy consent", scheduler_health.render_health(snapshot))
        coordinator.assert_not_called()

    def test_queue_attention_beats_plain_no_ready_work(self):
        snapshot = compute([qstate(176, "attention")])

        self.assertEqual(snapshot.state, "ATTENTION_REQUIRED")
        self.assertEqual(snapshot.attention_kind, "manual-or-queue-attention")


class SchedulerHealthNotificationTests(unittest.TestCase):
    def _registration_file(self, root: Path) -> Path:
        path = root / "registration.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def test_transition_into_empty_queue_notifies_once_then_stays_quiet(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration = self._registration_file(Path(temp_dir))
            policy = scheduler_health.NotificationPolicy(
                backend=scheduler_health.NOTIFICATION_NATIVE
            )
            calls: list[str] = []

            def notifier(_title: str, message: str) -> scheduler_health.NotificationResult:
                calls.append(message)
                return scheduler_health.NotificationResult(True, True, "native")

            ready = compute([qstate(1, "ready")])
            empty = compute([])
            scheduler_health.observe_health(
                registration,
                ready,
                policy=policy,
                notifier=notifier,
                now=NOW,
            )
            scheduler_health.observe_health(
                registration,
                empty,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(minutes=15),
            )
            scheduler_health.observe_health(
                registration,
                empty,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(minutes=30),
            )

            self.assertEqual(len(calls), 1)
            self.assertIn("NO_READY_WORK", calls[0])

    def test_transition_back_to_ready_updates_fingerprint_and_notifies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration = self._registration_file(Path(temp_dir))
            policy = scheduler_health.NotificationPolicy(
                backend=scheduler_health.NOTIFICATION_NATIVE
            )
            calls: list[str] = []

            def notifier(_title: str, message: str) -> scheduler_health.NotificationResult:
                calls.append(message)
                return scheduler_health.NotificationResult(True, True, "native")

            empty = compute([])
            ready = compute([qstate(1, "ready")])
            scheduler_health.observe_health(
                registration,
                empty,
                policy=policy,
                notifier=notifier,
                now=NOW,
            )
            scheduler_health.observe_health(
                registration,
                ready,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(minutes=15),
            )

            self.assertNotEqual(empty.fingerprint, ready.fingerprint)
            self.assertEqual(len(calls), 1)
            self.assertIn("READY_WORK_AVAILABLE", calls[0])

    def test_attention_cooldown_can_renotify_without_tick_spam(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration = self._registration_file(Path(temp_dir))
            policy = scheduler_health.NotificationPolicy(
                backend=scheduler_health.NOTIFICATION_NATIVE,
                reminder_hours=24,
            )
            calls: list[str] = []

            def notifier(_title: str, message: str) -> scheduler_health.NotificationResult:
                calls.append(message)
                return scheduler_health.NotificationResult(True, True, "native")

            attention = compute([qstate(176, "attention")])
            scheduler_health.observe_health(
                registration,
                attention,
                policy=policy,
                notifier=notifier,
                now=NOW,
            )
            scheduler_health.observe_health(
                registration,
                attention,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(hours=1),
            )
            scheduler_health.observe_health(
                registration,
                attention,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(hours=25),
            )

            self.assertEqual(len(calls), 2)

    def test_notification_failure_does_not_corrupt_health_or_repeat_each_tick(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration = self._registration_file(Path(temp_dir))
            policy = scheduler_health.NotificationPolicy(
                backend=scheduler_health.NOTIFICATION_NATIVE
            )
            calls = 0

            def notifier(_title: str, _message: str) -> scheduler_health.NotificationResult:
                nonlocal calls
                calls += 1
                return scheduler_health.NotificationResult(
                    True,
                    False,
                    "native",
                    "desktop unavailable",
                )

            attention = compute([qstate(176, "attention")])
            scheduler_health.observe_health(
                registration,
                attention,
                policy=policy,
                notifier=notifier,
                now=NOW,
            )
            scheduler_health.observe_health(
                registration,
                attention,
                policy=policy,
                notifier=notifier,
                now=NOW + timedelta(minutes=15),
            )

            record = json.loads(scheduler_health.health_path(registration).read_text(encoding="utf-8"))
            self.assertEqual(calls, 1)
            self.assertEqual(record["current"]["state"], "ATTENTION_REQUIRED")
            self.assertFalse(record["last_notification"]["delivered"])

    def test_notification_payload_contains_no_prompt_source_or_secret_content(self):
        snapshot = compute(
            [qstate(57, "ready")],
            privacy_blocked=True,
            grants={"active": 0, "expired": 1, "revoked": 0},
        )
        title, message = scheduler_health._notification_message(snapshot)
        combined = (title + " " + message).casefold()

        self.assertIn("owner/repo", combined)
        self.assertIn("#57", combined)
        for forbidden in ("prompt", "source code", "api key", "credential", "secret-value"):
            self.assertNotIn(forbidden, combined)


class SchedulerHealthCliTests(unittest.TestCase):
    def test_run_tick_preserves_scheduler_exit_code_when_health_reporting_fails(self):
        output = io.StringIO()
        error = io.StringIO()
        with patch.object(
            scheduler_health,
            "_resolve_registration",
            side_effect=scheduler_health.SchedulerHealthError("missing"),
        ), patch.object(
            scheduler_health.scheduler,
            "run_cli",
            return_value=23,
        ) as run_scheduler:
            code = scheduler_health.run_tick(
                ["run-once", "--registration", "missing.json"],
                stdout=output,
                stderr=error,
            )

        self.assertEqual(code, 23)
        run_scheduler.assert_called_once()

    def test_notification_policy_is_explicit_opt_in_and_secret_free(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration = self._registration_file(Path(temp_dir))
            self.assertEqual(
                scheduler_health.load_notification_policy(registration).backend,
                scheduler_health.NOTIFICATION_OFF,
            )

            policy = scheduler_health.NotificationPolicy(
                backend=scheduler_health.NOTIFICATION_NATIVE,
                reminder_hours=48,
            )
            scheduler_health.save_notification_policy(registration, policy)
            text = scheduler_health.notification_path(registration).read_text(encoding="utf-8")

            self.assertIn('"backend": "native"', text)
            self.assertIn('"reminder_hours": 48', text)
            self.assertNotIn("route_identities", text)
            self.assertNotIn("credential", text.casefold())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import distributed_claims, queue_selection, scheduler


def make_registration(root: Path) -> tuple[Path, scheduler.SchedulerRegistration]:
    root = root.expanduser().resolve()
    worker = root / "worker"
    worker.mkdir(parents=True)
    (worker / ".git").mkdir()
    path = root / "scheduler" / scheduler.REGISTRATION_FILE
    registration = scheduler.SchedulerRegistration(
        github_repository="owner/repo",
        source_repository=str(root / "source"),
        worker_repository=str(worker),
        default_branch="main",
        backend=scheduler.BACKEND_CRON,
        cadence_minutes=15,
        launcher=str(root / "bin" / "autodev"),
        task_id="autodev-owner-repo",
        installed_at="2026-08-23T08:00:00Z",
    )
    scheduler._write_registration(path, registration)
    return path, registration


def claim(issue: int, worker: str = "worker-a") -> distributed_claims.Claim:
    return distributed_claims.Claim(
        repository="owner/repo",
        issue_number=issue,
        worker_id=worker,
        run_id=f"run-{issue}",
        claim_id=f"claim-{issue}-{worker}",
        acquired_at="2026-08-23T08:00:00Z",
        heartbeat_at="2026-08-23T08:00:00Z",
        lease_seconds=7200,
        ref=distributed_claims.claim_ref(issue),
        sha=("a" if worker == "worker-a" else "b") * 40,
    )


class DummyLease:
    def __init__(self, _repo: Path, owned: distributed_claims.Claim, **_kwargs):
        self.claim = owned
        self.lost = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def latest_claim(self):
        return self.claim


class SchedulerClaimTests(unittest.TestCase):
    def _common(self):
        return (
            patch.object(scheduler, "_prepare_worker", return_value=queue_selection.ExistingRun("NONE")),
            patch.object(
                scheduler.distributed_claims,
                "load_claim_policy",
                return_value=distributed_claims.ClaimPolicy(max_concurrent_issues=2, lease_minutes=120),
            ),
            patch.object(
                scheduler.distributed_claims,
                "worker_identity",
                return_value=distributed_claims.WorkerIdentity("worker-a"),
            ),
            patch.object(scheduler.distributed_claims, "reconcile_stale_claims"),
            patch.object(scheduler.distributed_claims, "HeartbeatLease", DummyLease),
        )

    def test_losing_claim_race_reselects_next_eligible_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration_file, registration = make_registration(Path(temp_dir))
            output = io.StringIO()
            seen_exclusions: list[set[int]] = []
            acquired_second = claim(2)

            def select_next(_repo, _github_repo, **kwargs):
                excluded = set(kwargs.get("excluded_issue_numbers", set()))
                seen_exclusions.append(excluded)
                issue = 2 if 1 in excluded else 1
                return queue_selection.SelectionResult(
                    state="SELECTED",
                    repository="owner/repo",
                    issue_number=issue,
                    issue_title=f"Issue {issue}",
                    explanation="deterministic selection",
                )

            def acquire(_repo, _github_repo, issue_number, *_args, **_kwargs):
                if issue_number == 1:
                    return distributed_claims.ClaimAttempt(
                        "BUSY",
                        owner=claim(1, "worker-b"),
                        detail="race lost",
                    )
                return distributed_claims.ClaimAttempt(
                    "ACQUIRED",
                    claim=acquired_second,
                    owner=acquired_second,
                )

            calls: list[list[str]] = []
            def coordinator(argv: list[str]) -> int:
                calls.append(list(argv))
                return 0

            common = self._common()
            with common[0], common[1], common[2], common[3], common[4], patch.object(
                scheduler.distributed_claims,
                "list_claims",
                side_effect=[(), (), ()],
            ), patch.object(
                scheduler.queue_selection,
                "select_next",
                side_effect=select_next,
            ), patch.object(
                scheduler.distributed_claims,
                "acquire_claim",
                side_effect=acquire,
            ), patch.object(
                scheduler,
                "_coordinator_state",
                return_value="ReadyForReview",
            ), patch.object(
                scheduler.distributed_claims,
                "release_claim",
                return_value=True,
            ):
                code = scheduler.run_once(
                    registration_file,
                    home=Path(temp_dir),
                    coordinator=coordinator,
                    stdout=output,
                )

            self.assertEqual(code, 0)
            self.assertEqual(seen_exclusions[0], set())
            self.assertEqual(seen_exclusions[1], {1})
            self.assertEqual(
                calls,
                [[
                    "coordinate",
                    "--repo",
                    registration.worker_repository,
                    "--arguments",
                    "2",
                ]],
            )
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["state"], "PR_READY")
            self.assertEqual(payload["issue_number"], 2)
            self.assertEqual(payload["claim_state"], "RELEASED")

    def test_full_distributed_capacity_is_a_successful_non_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration_file, _registration = make_registration(Path(temp_dir))
            output = io.StringIO()
            claims = (claim(1), claim(2, "worker-b"))
            common = self._common()
            with common[0], common[1], common[2], common[3], common[4], patch.object(
                scheduler.distributed_claims,
                "list_claims",
                return_value=claims,
            ), patch.object(
                scheduler.opencode_entrypoint,
                "run",
            ) as coordinator:
                code = scheduler.run_once(
                    registration_file,
                    home=Path(temp_dir),
                    stdout=output,
                )

            self.assertEqual(code, 0)
            coordinator.assert_not_called()
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["state"], "NO_CAPACITY")
            self.assertIn("2/2", payload["detail"])

    def test_resumable_local_run_refuses_another_workers_active_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registration_file, _registration = make_registration(Path(temp_dir))
            error = io.StringIO()
            existing = queue_selection.ExistingRun(
                "RESUME_EXISTING",
                issue_number=42,
                branch="autodev/issue-42-work",
                next_stage="semantic",
                next_action="verifier",
                reason="resume first",
            )
            busy = distributed_claims.ClaimAttempt(
                "BUSY",
                owner=claim(42, "worker-b"),
                detail="owned elsewhere",
            )
            with patch.object(scheduler, "_prepare_worker", return_value=existing), patch.object(
                scheduler.distributed_claims,
                "load_claim_policy",
                return_value=distributed_claims.ClaimPolicy(max_concurrent_issues=2),
            ), patch.object(
                scheduler.distributed_claims,
                "worker_identity",
                return_value=distributed_claims.WorkerIdentity("worker-a"),
            ), patch.object(scheduler.distributed_claims, "reconcile_stale_claims"), patch.object(
                scheduler.distributed_claims,
                "list_claims",
                return_value=(busy.owner,),
            ), patch.object(
                scheduler.queue_selection,
                "select_next",
                return_value=queue_selection.SelectionResult(
                    state="RESUME_EXISTING",
                    repository="owner/repo",
                    issue_number=42,
                    source="existing-run",
                    explanation="resume first",
                ),
            ), patch.object(
                scheduler.distributed_claims,
                "acquire_claim",
                return_value=busy,
            ), patch.object(
                scheduler.opencode_entrypoint,
                "run",
            ) as coordinator:
                code = scheduler.run_once(
                    registration_file,
                    home=Path(temp_dir),
                    stderr=error,
                )

            self.assertEqual(code, 2)
            coordinator.assert_not_called()
            payload = json.loads(error.getvalue())
            self.assertEqual(payload["state"], "CLAIM_CONFLICT")
            self.assertIn("worker-b", payload["detail"])

    def test_terminal_attention_releases_claim_but_waiting_state_keeps_it(self):
        for durable_state, expected_dispatch, expected_releases in (
            ("AttentionRequired", "ATTENTION_REQUIRED", 1),
            ("WaitingForCI", "DISPATCHED", 0),
        ):
            with self.subTest(durable_state=durable_state), tempfile.TemporaryDirectory() as temp_dir:
                registration_file, _registration = make_registration(Path(temp_dir))
                output = io.StringIO()
                owned = claim(7)
                common = self._common()
                with common[0], common[1], common[2], common[3], common[4], patch.object(
                    scheduler.distributed_claims,
                    "list_claims",
                    return_value=(),
                ), patch.object(
                    scheduler.queue_selection,
                    "select_next",
                    return_value=queue_selection.SelectionResult(
                        state="SELECTED",
                        repository="owner/repo",
                        issue_number=7,
                    ),
                ), patch.object(
                    scheduler.distributed_claims,
                    "acquire_claim",
                    return_value=distributed_claims.ClaimAttempt(
                        "ACQUIRED",
                        claim=owned,
                        owner=owned,
                    ),
                ), patch.object(
                    scheduler,
                    "_coordinator_state",
                    return_value=durable_state,
                ), patch.object(
                    scheduler.distributed_claims,
                    "release_claim",
                    return_value=True,
                ) as release:
                    code = scheduler.run_once(
                        registration_file,
                        home=Path(temp_dir),
                        coordinator=lambda _argv: 0,
                        stdout=output,
                    )

                self.assertEqual(code, 0)
                self.assertEqual(release.call_count, expected_releases)
                payload = json.loads(output.getvalue())
                self.assertEqual(payload["state"], expected_dispatch)

    def test_lost_heartbeat_claim_fails_closed_after_coordinator_returns(self):
        class LostLease(DummyLease):
            def __init__(self, repo, owned, **kwargs):
                super().__init__(repo, owned, **kwargs)
                self.lost = True

        with tempfile.TemporaryDirectory() as temp_dir:
            registration_file, _registration = make_registration(Path(temp_dir))
            error = io.StringIO()
            owned = claim(8)
            common = self._common()
            with common[0], common[1], common[2], common[3], patch.object(
                scheduler.distributed_claims,
                "HeartbeatLease",
                LostLease,
            ), patch.object(
                scheduler.distributed_claims,
                "list_claims",
                return_value=(),
            ), patch.object(
                scheduler.queue_selection,
                "select_next",
                return_value=queue_selection.SelectionResult(
                    state="SELECTED",
                    repository="owner/repo",
                    issue_number=8,
                ),
            ), patch.object(
                scheduler.distributed_claims,
                "acquire_claim",
                return_value=distributed_claims.ClaimAttempt(
                    "ACQUIRED",
                    claim=owned,
                    owner=owned,
                ),
            ), patch.object(scheduler, "_coordinator_state", return_value="WaitingForCI"):
                code = scheduler.run_once(
                    registration_file,
                    home=Path(temp_dir),
                    coordinator=lambda _argv: 0,
                    stderr=error,
                )

            self.assertEqual(code, 2)
            self.assertEqual(json.loads(error.getvalue())["state"], "CLAIM_CONFLICT")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import automation.claim_recovery as claim_recovery

from automation import claim_contract, claim_identity, claim_lease, claim_recovery, claim_repository

import json
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch



NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


def run_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed ({completed.returncode}): {completed.stderr or completed.stdout}"
        )
    return completed.stdout.strip()


def make_remote(root: Path) -> tuple[Path, Path, Path]:
    origin = root / "origin.git"
    seed = root / "seed"
    worker_a = root / "worker-a"
    worker_b = root / "worker-b"
    run_git(root, "init", "--bare", str(origin))
    seed.mkdir()
    run_git(seed, "init")
    run_git(seed, "config", "user.name", "Test User")
    run_git(seed, "config", "user.email", "test@example.invalid")
    (seed / "README.md").write_text("claim fixture\n", encoding="utf-8")
    run_git(seed, "add", "README.md")
    run_git(seed, "commit", "-m", "seed")
    run_git(seed, "branch", "-M", "main")
    run_git(seed, "remote", "add", "origin", str(origin))
    run_git(seed, "push", "-u", "origin", "main")
    run_git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    run_git(root, "clone", str(origin), str(worker_a))
    run_git(root, "clone", str(origin), str(worker_b))
    return origin, worker_a, worker_b


class DistributedClaimGitTests(unittest.TestCase):
    def test_two_workers_race_for_one_issue_and_exactly_one_wins(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            barrier = threading.Barrier(2)
            results: list[claim_contract.ClaimAttempt] = []
            errors: list[BaseException] = []

            def attempt(worker: Path, worker_id: str) -> None:
                try:
                    barrier.wait(timeout=5)
                    results.append(
                        claim_lease.acquire_claim(
                            worker,
                            "owner/repo",
                            42,
                            worker_id,
                            "origin/main",
                            policy=claim_contract.ClaimPolicy(
                                max_concurrent_issues=2,
                                lease_minutes=30,
                            ),
                            now=NOW,
                            evidence_checker=lambda *_args: (),
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)

            first = threading.Thread(target=attempt, args=(worker_a, "mega-beast"))
            second = threading.Thread(target=attempt, args=(worker_b, "laptop"))
            first.start()
            second.start()
            first.join(timeout=15)
            second.join(timeout=15)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(sum(item.state == "ACQUIRED" for item in results), 1)
            self.assertEqual(sum(item.state == "BUSY" for item in results), 1)
            winner = claim_repository.get_claim(worker_a, 42)
            self.assertIsNotNone(winner)
            self.assertIn(winner.worker_id, {"mega-beast", "laptop"})

    def test_losing_worker_can_claim_another_ready_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            policy = claim_contract.ClaimPolicy(max_concurrent_issues=2, lease_minutes=30)
            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                1,
                "mega-beast",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            lost = claim_lease.acquire_claim(
                worker_b,
                "owner/repo",
                1,
                "laptop",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            second = claim_lease.acquire_claim(
                worker_b,
                "owner/repo",
                2,
                "laptop",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )

            self.assertEqual(first.state, "ACQUIRED")
            self.assertEqual(lost.state, "BUSY")
            self.assertEqual(second.state, "ACQUIRED")

    def test_same_worker_resume_renews_and_reuses_claim_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, _worker_b = make_remote(Path(temp_dir))
            policy = claim_contract.ClaimPolicy(lease_minutes=30)
            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                7,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            second = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                7,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=5),
                evidence_checker=lambda *_args: (),
            )

            self.assertEqual(first.state, "ACQUIRED")
            self.assertEqual(second.state, "OWNED")
            self.assertEqual(first.claim.claim_id, second.claim.claim_id)
            self.assertEqual(first.claim.run_id, second.claim.run_id)
            self.assertNotEqual(first.claim.sha, second.claim.sha)

    def test_heartbeat_renewal_prevents_stale_takeover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            policy = claim_contract.ClaimPolicy(lease_minutes=15)
            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                8,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            renewed = claim_lease.renew_claim(
                worker_a,
                first.claim,
                now=NOW + timedelta(minutes=10),
            )
            attempted = claim_lease.acquire_claim(
                worker_b,
                "owner/repo",
                8,
                "worker-b",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=16),
                evidence_checker=lambda *_args: (),
            )

            self.assertIsNotNone(renewed)
            self.assertEqual(attempted.state, "BUSY")
            self.assertEqual(attempted.owner.worker_id, "worker-a")

    def test_stale_claim_can_be_recovered_with_exact_ref_lease(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            policy = claim_contract.ClaimPolicy(lease_minutes=15)
            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                9,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            recovered = claim_lease.acquire_claim(
                worker_b,
                "owner/repo",
                9,
                "worker-b",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=16),
                evidence_checker=lambda *_args: (),
            )

            self.assertEqual(first.state, "ACQUIRED")
            self.assertEqual(recovered.state, "ACQUIRED")
            self.assertEqual(recovered.claim.worker_id, "worker-b")
            self.assertNotEqual(first.claim.claim_id, recovered.claim.claim_id)

    def test_stale_recovery_refuses_existing_branch_or_pr_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            policy = claim_contract.ClaimPolicy(lease_minutes=15)
            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                10,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            protected = claim_lease.acquire_claim(
                worker_b,
                "owner/repo",
                10,
                "worker-b",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=16),
                evidence_checker=lambda *_args: ("open AutoDev PR exists",),
            )

            self.assertEqual(first.state, "ACQUIRED")
            self.assertEqual(protected.state, "STALE_PROTECTED")
            current = claim_repository.get_claim(worker_b, 10)
            self.assertEqual(current.claim_id, first.claim.claim_id)

    def test_release_is_compare_and_swap_and_does_not_delete_another_claim(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            policy = claim_contract.ClaimPolicy(lease_minutes=15)
            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                11,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            second = claim_lease.acquire_claim(
                worker_b,
                "owner/repo",
                11,
                "worker-b",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=16),
                evidence_checker=lambda *_args: (),
            )

            self.assertEqual(second.state, "ACQUIRED")
            self.assertFalse(claim_lease.release_claim(worker_a, first.claim))
            self.assertEqual(claim_repository.get_claim(worker_a, 11).worker_id, "worker-b")
            self.assertTrue(claim_lease.release_claim(worker_b, second.claim))
            self.assertIsNone(claim_repository.get_claim(worker_a, 11))

    def test_claim_metadata_is_secret_free_and_contains_required_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _origin, worker_a, _worker_b = make_remote(root)
            attempt = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                12,
                "mega-beast",
                "origin/main",
                policy=claim_contract.ClaimPolicy(lease_minutes=30),
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            claim = attempt.claim
            text = json.dumps(claim.to_json(), sort_keys=True)

            self.assertEqual(claim.repository, "owner/repo")
            self.assertEqual(claim.issue_number, 12)
            self.assertEqual(claim.worker_id, "mega-beast")
            self.assertTrue(claim.run_id)
            self.assertTrue(claim.claim_id)
            self.assertIn("2026-08-23", claim.acquired_at)
            self.assertNotIn(str(root), text)
            for forbidden in ("token", "password", "credential", "api_key", "home"):
                self.assertNotIn(forbidden, text.casefold())


class DistributedClaimPolicyTests(unittest.TestCase):
    def test_worker_identity_is_generated_stably_without_hostname_or_home_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            first = claim_identity.worker_identity(home=home)
            second = claim_identity.worker_identity(home=home)
            text = claim_identity.worker_state_path(home=home).read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertRegex(first.worker_id, r"^worker-[0-9a-f]{12}$")
            self.assertNotIn(str(home), text)

    def test_worker_identity_can_be_configured_explicitly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            identity = claim_identity.set_worker_identity(
                "mega-beast",
                home=Path(temp_dir),
            )
            self.assertEqual(identity.worker_id, "mega-beast")

    def test_queue_claim_policy_is_bounded_and_backwards_compatible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".autodev").mkdir()
            policy_path = repo / ".autodev" / "queue.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "autonomous_execution": True,
                        "max_concurrent_issues": 3,
                        "claim_lease_minutes": 45,
                    }
                ),
                encoding="utf-8",
            )
            policy = claim_identity.load_claim_policy(repo)

            self.assertEqual(policy.max_concurrent_issues, 3)
            self.assertEqual(policy.lease_minutes, 45)

            policy_path.write_text(
                json.dumps({"version": 1, "autonomous_execution": True}),
                encoding="utf-8",
            )
            legacy = claim_identity.load_claim_policy(repo)
            self.assertEqual(legacy.max_concurrent_issues, 1)
            self.assertEqual(legacy.lease_minutes, 120)

    def test_stale_reconcile_restores_running_label_when_claim_cas_loses(self):
        stale = claim_contract.Claim(
            repository="owner/repo",
            issue_number=13,
            worker_id="worker-a",
            run_id="run",
            claim_id="claim",
            acquired_at="2026-08-23T07:00:00Z",
            heartbeat_at="2026-08-23T07:00:00Z",
            lease_seconds=900,
            ref=claim_contract.claim_ref(13),
            sha="a" * 40,
        )
        label_calls: list[bool] = []
        with patch.object(claim_recovery, "list_claims", return_value=(stale,)), patch.object(claim_repository, "_delete_with_lease",
            return_value=False,
        ), patch.object(claim_recovery, "_set_running_label",
            side_effect=lambda *_args, enabled, **_kwargs: label_calls.append(enabled) or True,
        ):
            result = claim_recovery.reconcile_stale_claims(
                Path("."),
                "owner/repo",
                now=NOW,
                evidence_checker=lambda *_args: (),
            )

        self.assertEqual(result.raced, (13,))
        self.assertEqual(label_calls, [False, True])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation import claim_contract, claim_lease, claim_repository


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


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
            f"git {' '.join(args)} failed ({completed.returncode}): "
            f"{completed.stderr or completed.stdout}"
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


class DistributedClaimHistoryTests(unittest.TestCase):
    def test_one_hundred_renewals_keep_only_one_claim_commit_above_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, _worker_b = make_remote(Path(temp_dir))
            attempt = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                264,
                "worker-a",
                "origin/main",
                policy=claim_contract.ClaimPolicy(lease_minutes=120),
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            claim = attempt.claim
            base_sha = run_git(worker_a, "rev-parse", "origin/main")

            for index in range(1, 101):
                previous = claim
                claim = claim_lease.renew_claim(
                    worker_a,
                    claim,
                    now=NOW + timedelta(minutes=index),
                )
                self.assertIsNotNone(claim)
                self.assertNotEqual(previous.sha, claim.sha)
                self.assertEqual(claim.claim_id, attempt.claim.claim_id)
                self.assertEqual(claim.run_id, attempt.claim.run_id)

            current = claim_repository.get_claim(worker_a, 264)
            self.assertEqual(current.sha, claim.sha)
            self.assertEqual(
                run_git(worker_a, "rev-list", "--count", claim.sha, f"^{base_sha}"),
                "1",
            )
            self.assertEqual(run_git(worker_a, "rev-parse", f"{claim.sha}^"), base_sha)

    def test_stale_renewal_loses_exact_sha_compare_and_swap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            attempt = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                265,
                "worker-a",
                "origin/main",
                policy=claim_contract.ClaimPolicy(lease_minutes=120),
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            stale = attempt.claim
            fresh = claim_lease.renew_claim(
                worker_a,
                stale,
                now=NOW + timedelta(minutes=1),
            )
            self.assertIsNotNone(fresh)

            run_git(worker_b, "fetch", "origin", stale.ref)
            lost = claim_lease.renew_claim(
                worker_b,
                stale,
                now=NOW + timedelta(minutes=2),
            )
            self.assertIsNone(lost)
            current = claim_repository.get_claim(worker_b, 265)
            self.assertEqual(current.sha, fresh.sha)
            self.assertEqual(current.heartbeat_at, fresh.heartbeat_at)

    def test_first_new_renewal_collapses_legacy_heartbeat_chain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, _worker_b = make_remote(Path(temp_dir))
            attempt = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                266,
                "worker-a",
                "origin/main",
                policy=claim_contract.ClaimPolicy(lease_minutes=120),
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            claim = attempt.claim
            base_sha = run_git(worker_a, "rev-parse", "origin/main")

            # Reproduce the old implementation: each replacement claim commit
            # is parented by the previous heartbeat commit.
            for index in range(1, 6):
                heartbeat = NOW + timedelta(minutes=index)
                metadata = claim_repository._claim_metadata(
                    github_repo=claim.repository,
                    issue_number=claim.issue_number,
                    worker_id=claim.worker_id,
                    run_id=claim.run_id,
                    claim_id=claim.claim_id,
                    acquired_at=claim.acquired_at,
                    heartbeat_at=claim_contract._iso(heartbeat),
                    lease_seconds=claim.lease_seconds,
                )
                legacy_sha = claim_repository._create_claim_commit(
                    worker_a,
                    claim.sha,
                    metadata,
                    runner=subprocess.run,
                )
                pushed = claim_repository._push_with_lease(
                    worker_a,
                    ref=claim.ref,
                    new_sha=legacy_sha,
                    expected_sha=claim.sha,
                    runner=subprocess.run,
                )
                self.assertTrue(pushed)
                claim = claim.__class__(
                    repository=claim.repository,
                    issue_number=claim.issue_number,
                    worker_id=claim.worker_id,
                    run_id=claim.run_id,
                    claim_id=claim.claim_id,
                    acquired_at=claim.acquired_at,
                    heartbeat_at=claim_contract._iso(heartbeat),
                    lease_seconds=claim.lease_seconds,
                    ref=claim.ref,
                    sha=legacy_sha,
                )

            self.assertEqual(
                run_git(worker_a, "rev-list", "--count", claim.sha, f"^{base_sha}"),
                "6",
            )

            renewed = claim_lease.renew_claim(
                worker_a,
                claim,
                now=NOW + timedelta(minutes=10),
            )
            self.assertIsNotNone(renewed)
            self.assertEqual(
                run_git(worker_a, "rev-list", "--count", renewed.sha, f"^{base_sha}"),
                "1",
            )
            self.assertEqual(run_git(worker_a, "rev-parse", f"{renewed.sha}^"), base_sha)


if __name__ == "__main__":
    unittest.main()

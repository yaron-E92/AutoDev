from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation import claim_contract, claim_lease


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def git(cwd: Path, *args: str) -> None:
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
        raise AssertionError(completed.stderr or completed.stdout)


def fixture(root: Path) -> Path:
    origin = root / "origin.git"
    seed = root / "seed"
    worker = root / "worker"
    git(root, "init", "--bare", str(origin))
    seed.mkdir()
    git(seed, "init")
    git(seed, "config", "user.name", "Test")
    git(seed, "config", "user.email", "test@example.invalid")
    (seed / "README.md").write_text("fixture\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "-m", "seed")
    git(seed, "branch", "-M", "main")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-u", "origin", "main")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    git(root, "clone", str(origin), str(worker))
    return worker


class SameOwnerExpiredLeaseLivenessTests(unittest.TestCase):
    def test_expired_same_owner_claim_keeps_elapsed_no_progress_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = fixture(Path(temp_dir))
            policy = claim_contract.ClaimPolicy(
                lease_minutes=15,
                max_no_progress_attempts=100,
                max_no_progress_minutes=30,
            )
            first = claim_lease.acquire_claim(
                worker,
                "owner/repo",
                42,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            self.assertEqual(first.state, "ACQUIRED")

            # The lease has been expired for 16 minutes here. The same owner must
            # still be judged against the original durable-progress timestamp,
            # rather than deleting/recreating the claim with a fresh budget.
            with self.assertRaisesRegex(claim_contract.ClaimError, r"RUN_STALLED issue #42"):
                claim_lease.acquire_claim(
                    worker,
                    "owner/repo",
                    42,
                    "worker-a",
                    "origin/main",
                    policy=policy,
                    now=NOW + timedelta(minutes=31),
                    evidence_checker=lambda *_args: (),
                )


if __name__ == "__main__":
    unittest.main()

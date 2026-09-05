from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from automation import (
    claim_contract,
    claim_identity,
    claim_lease,
    claim_liveness,
    claim_recovery,
    claim_repository,
    scheduler,
)


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
    (seed / "README.md").write_text("claim liveness fixture\n", encoding="utf-8")
    run_git(seed, "add", "README.md")
    run_git(seed, "commit", "-m", "seed")
    run_git(seed, "branch", "-M", "main")
    run_git(seed, "remote", "add", "origin", str(origin))
    run_git(seed, "push", "-u", "origin", "main")
    run_git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    run_git(root, "clone", str(origin), str(worker_a))
    run_git(root, "clone", str(origin), str(worker_b))
    return origin, worker_a, worker_b


def current_dir(repo: Path) -> Path:
    path = repo / ".autodev-run" / "current"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_state(
    repo: Path,
    *,
    issue_number: int = 42,
    status: str = "Prepared",
    last_commit_sha: str = "",
    repair_attempts: int = 0,
    volatile_marker: str = "first",
) -> None:
    state = {
        "Status": status,
        "IssueNumber": issue_number,
        "RepoFullName": "owner/repo",
        "BranchName": f"autodev/issue-{issue_number}-work",
        "DevelopmentStrategy": "git-flow",
        "IntegrationBranch": "develop",
        "ReleaseBranch": "main",
        "Base": "develop",
        "BaseSha": "a" * 40,
        "BaseTreeSha": "b" * 40,
        "PreparedLocalHeadSha": "a" * 40,
        "PreparedSnapshotHash": "c" * 64,
        "LastCommitSha": last_commit_sha,
        "PrNumber": 0,
        "PrHeadSha": "",
        "LastLocalCheckPassed": False,
        "RepairAttemptCount": repair_attempts,
        # These values are deliberately volatile or model/user prose and must not
        # become workflow-progress evidence.
        "CreatedAt": f"2026-09-05T12:00:00Z-{volatile_marker}",
        "Timestamp": volatile_marker,
        "IssueText": f"customer prose changed: {volatile_marker}",
        "LastFailureOutput": f"volatile log text {volatile_marker}",
        "SomeSecretLookingField": f"do-not-fingerprint-{volatile_marker}",
    }
    (current_dir(repo) / "state.json").write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_manifest(
    repo: Path,
    *,
    issue_number: int = 42,
    current_stage: str = "implementation-generated",
    completed_stages: list[str] | None = None,
    output_hash: str = "d" * 64,
    volatile_marker: str = "first",
) -> None:
    completed = completed_stages or ["issue-selected", "repository-read"]
    manifest = {
        "schema_version": 1,
        "run_id": f"run-{issue_number}",
        "created_at": "2026-09-05T11:00:00Z",
        "updated_at": f"2026-09-05T12:00:00Z-{volatile_marker}",
        "target": {
            "repo_path": str(repo),
            "github_repo": "owner/repo",
            "issue_number": issue_number,
            "mode": "full",
            "base_sha": "a" * 40,
            "branch": f"autodev/issue-{issue_number}-work",
        },
        "current_stage": current_stage,
        "completed_stages": completed,
        "stages": {
            "repository-read": {
                "status": "completed",
                "completed_at": f"2026-09-05T11:10:00Z-{volatile_marker}",
                "input_hash": "e" * 64,
                "output_hash": output_hash,
                "artifacts": {"reader.md": "f" * 64},
                "details": {"summary": f"model prose {volatile_marker}"},
            }
        },
        "roles": {},
        "prompt_policy": {},
        "semantic_verification": {},
        "ux_artifact": {},
        "invocations": [{"stdout": f"volatile invocation {volatile_marker}"}],
        "failure": {},
        "pr": {},
        "invalidations": [],
    }
    (current_dir(repo) / "run-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class ClaimProgressIdentityTests(unittest.TestCase):
    def test_progress_identity_excludes_volatile_prose_and_tracks_durable_advance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            write_state(repo, volatile_marker="first")
            write_manifest(repo, volatile_marker="first")

            first = claim_liveness.progress_snapshot(repo, 42)

            write_state(repo, volatile_marker="second")
            write_manifest(repo, volatile_marker="second")
            second = claim_liveness.progress_snapshot(repo, 42)
            self.assertEqual(first.identity, second.identity)
            self.assertFalse(second.terminal)

            write_state(repo, repair_attempts=1, volatile_marker="third")
            write_manifest(repo, volatile_marker="third")
            advanced = claim_liveness.progress_snapshot(repo, 42)
            self.assertNotEqual(second.identity, advanced.identity)

            write_state(
                repo,
                status="ReadyForReview",
                repair_attempts=1,
                volatile_marker="fourth",
            )
            write_manifest(repo, volatile_marker="fourth")
            terminal = claim_liveness.progress_snapshot(repo, 42)
            self.assertTrue(terminal.terminal)

    def test_terminal_checkpoint_for_another_issue_never_releases_stalled_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            write_state(repo, issue_number=99, status="ReadyForReview")
            write_manifest(repo, issue_number=99, current_stage="pr-created")

            snapshot = claim_liveness.progress_snapshot(repo, 42)

            self.assertFalse(snapshot.terminal)
            self.assertIn("status=different-issue", snapshot.summary)


class ClaimNoProgressTests(unittest.TestCase):
    def test_repeated_ownership_stalls_without_extending_heartbeat_and_progress_resets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, worker_b = make_remote(Path(temp_dir))
            write_state(worker_a)
            write_manifest(worker_a)
            policy = claim_contract.ClaimPolicy(
                lease_minutes=120,
                max_no_progress_attempts=2,
                max_no_progress_minutes=360,
            )

            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                42,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            second = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                42,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=5),
                evidence_checker=lambda *_args: (),
            )

            self.assertEqual(first.state, "ACQUIRED")
            self.assertEqual(first.claim.no_progress_attempts, 0)
            self.assertEqual(second.state, "OWNED")
            self.assertEqual(second.claim.no_progress_attempts, 1)
            self.assertEqual(second.claim.progress_at, first.claim.progress_at)

            with self.assertRaisesRegex(
                claim_contract.ClaimError,
                r"RUN_STALLED issue #42.*no-progress attempts=2/2.*recovery:",
            ):
                claim_lease.acquire_claim(
                    worker_a,
                    "owner/repo",
                    42,
                    "worker-a",
                    "origin/main",
                    policy=policy,
                    now=NOW + timedelta(minutes=10),
                    evidence_checker=lambda *_args: (),
                )

            stalled = claim_repository.get_claim(worker_a, 42)
            self.assertIsNotNone(stalled)
            self.assertEqual(
                stalled.liveness_state,
                claim_contract.CLAIM_LIVENESS_STALLED,
            )
            self.assertEqual(stalled.no_progress_attempts, 2)
            # Publishing STALLED is metadata/state publication, not a lease
            # extension. The last healthy heartbeat remains authoritative.
            self.assertEqual(stalled.heartbeat_at, second.claim.heartbeat_at)
            self.assertEqual(stalled.progress_at, first.claim.progress_at)

            protected = claim_lease.acquire_claim(
                worker_b,
                "owner/repo",
                42,
                "worker-b",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=200),
                evidence_checker=lambda *_args: (),
            )
            self.assertEqual(protected.state, "STALE_PROTECTED")
            self.assertEqual(protected.owner.claim_id, stalled.claim_id)
            self.assertIn("stalled distributed claim", protected.detail)

            write_state(worker_a, last_commit_sha="9" * 40)
            write_manifest(worker_a)
            resumed = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                42,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW + timedelta(minutes=201),
                evidence_checker=lambda *_args: (),
            )
            self.assertEqual(resumed.state, "OWNED")
            self.assertEqual(
                resumed.claim.liveness_state,
                claim_contract.CLAIM_LIVENESS_ACTIVE,
            )
            self.assertEqual(resumed.claim.no_progress_attempts, 0)
            self.assertNotEqual(resumed.claim.progress_id, stalled.progress_id)

            heartbeat_one = claim_lease.renew_claim(
                worker_a,
                resumed.claim,
                now=NOW + timedelta(minutes=205),
            )
            heartbeat_two = claim_lease.renew_claim(
                worker_a,
                heartbeat_one,
                now=NOW + timedelta(minutes=210),
            )
            self.assertIsNotNone(heartbeat_one)
            self.assertIsNotNone(heartbeat_two)
            self.assertEqual(heartbeat_two.no_progress_attempts, 0)
            self.assertEqual(heartbeat_two.progress_id, resumed.claim.progress_id)
            self.assertNotEqual(heartbeat_two.heartbeat_at, resumed.claim.heartbeat_at)

    def test_elapsed_no_progress_bound_stalls_even_before_attempt_cap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, _worker_b = make_remote(Path(temp_dir))
            write_state(worker_a)
            write_manifest(worker_a)
            policy = claim_contract.ClaimPolicy(
                lease_minutes=600,
                max_no_progress_attempts=100,
                max_no_progress_minutes=30,
            )
            first = claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                42,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            self.assertEqual(first.state, "ACQUIRED")

            with self.assertRaisesRegex(claim_contract.ClaimError, r"RUN_STALLED issue #42"):
                claim_lease.acquire_claim(
                    worker_a,
                    "owner/repo",
                    42,
                    "worker-a",
                    "origin/main",
                    policy=policy,
                    now=NOW + timedelta(minutes=31),
                    evidence_checker=lambda *_args: (),
                )

            stalled = claim_repository.get_claim(worker_a, 42)
            self.assertEqual(stalled.no_progress_attempts, 1)
            self.assertEqual(
                stalled.liveness_state,
                claim_contract.CLAIM_LIVENESS_STALLED,
            )

    def test_stalled_claim_is_protected_until_same_issue_reaches_terminal_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _origin, worker_a, _worker_b = make_remote(Path(temp_dir))
            write_state(worker_a)
            write_manifest(worker_a)
            policy = claim_contract.ClaimPolicy(
                lease_minutes=120,
                max_no_progress_attempts=1,
                max_no_progress_minutes=360,
            )
            claim_lease.acquire_claim(
                worker_a,
                "owner/repo",
                42,
                "worker-a",
                "origin/main",
                policy=policy,
                now=NOW,
                evidence_checker=lambda *_args: (),
            )
            with self.assertRaises(claim_contract.ClaimError):
                claim_lease.acquire_claim(
                    worker_a,
                    "owner/repo",
                    42,
                    "worker-a",
                    "origin/main",
                    policy=policy,
                    now=NOW + timedelta(minutes=1),
                    evidence_checker=lambda *_args: (),
                )

            protected = claim_recovery.reconcile_stale_claims(
                worker_a,
                "owner/repo",
                now=NOW + timedelta(hours=5),
                evidence_checker=lambda *_args: (),
            )
            self.assertEqual(protected.protected, (42,))
            self.assertIsNotNone(claim_repository.get_claim(worker_a, 42))

            # A terminal checkpoint for a different issue is not permission to
            # discard the stalled claim.
            write_state(worker_a, issue_number=99, status="ReadyForReview")
            write_manifest(worker_a, issue_number=99, current_stage="pr-created")
            still_protected = claim_recovery.reconcile_stale_claims(
                worker_a,
                "owner/repo",
                now=NOW + timedelta(hours=6),
                evidence_checker=lambda *_args: (),
            )
            self.assertEqual(still_protected.protected, (42,))
            self.assertIsNotNone(claim_repository.get_claim(worker_a, 42))

            write_state(worker_a, issue_number=42, status="ReadyForReview")
            write_manifest(worker_a, issue_number=42, current_stage="pr-created")
            recovered = claim_recovery.reconcile_stale_claims(
                worker_a,
                "owner/repo",
                now=NOW + timedelta(hours=7),
                evidence_checker=lambda *_args: (),
            )
            self.assertEqual(recovered.recovered, (42,))
            self.assertIsNone(claim_repository.get_claim(worker_a, 42))


class ClaimNoProgressPolicyTests(unittest.TestCase):
    def test_queue_policy_supports_bounded_no_progress_overrides_and_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".autodev").mkdir()
            path = repo / ".autodev" / "queue.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "autonomous_execution": True,
                        "claim_max_no_progress_attempts": 9,
                        "claim_max_no_progress_minutes": 480,
                    }
                ),
                encoding="utf-8",
            )
            configured = claim_identity.load_claim_policy(repo)
            self.assertEqual(configured.max_no_progress_attempts, 9)
            self.assertEqual(configured.max_no_progress_minutes, 480)

            path.write_text(
                json.dumps({"version": 1, "autonomous_execution": True}),
                encoding="utf-8",
            )
            defaults = claim_identity.load_claim_policy(repo)
            self.assertEqual(
                defaults.max_no_progress_attempts,
                claim_contract.DEFAULT_MAX_NO_PROGRESS_ATTEMPTS,
            )
            self.assertEqual(
                defaults.max_no_progress_minutes,
                claim_contract.DEFAULT_MAX_NO_PROGRESS_MINUTES,
            )

            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "autonomous_execution": True,
                        "claim_max_no_progress_attempts": 0,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                claim_contract.ClaimError,
                r"claim_max_no_progress_attempts must be between",
            ):
                claim_identity.load_claim_policy(repo)


class SchedulerStalledOutcomeTests(unittest.TestCase):
    def test_run_stalled_claim_error_is_persisted_as_scheduler_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worker = root / "worker"
            worker.mkdir()
            (worker / ".git").mkdir()
            registration_file = root / "registration.json"
            registration = scheduler.SchedulerRegistration(
                github_repository="owner/repo",
                source_repository=str(root / "source"),
                worker_repository=str(worker),
                default_branch="develop",
                backend=scheduler.BACKEND_CRON,
                cadence_minutes=15,
                launcher=str(root / "autodev"),
                task_id="autodev-owner-repo",
                installed_at="2026-09-05T10:00:00Z",
            )
            scheduler._write_registration(registration_file, registration)
            error = io.StringIO()

            with patch.object(
                scheduler,
                "run_once",
                side_effect=claim_contract.ClaimError(
                    "RUN_STALLED issue #42; no-progress attempts=6/6; "
                    "recovery: run `autodev resume`"
                ),
            ):
                code = scheduler.run_cli(
                    ["run-once", "--registration", str(registration_file)],
                    stderr=error,
                )

            self.assertEqual(code, 2)
            self.assertIn("RUN_STALLED issue #42", error.getvalue())
            updated = scheduler._load_registration(registration_file)
            self.assertIsNotNone(updated)
            self.assertEqual(updated.last_run["state"], "SCHEDULER_ERROR")
            self.assertIn("RUN_STALLED issue #42", updated.last_run["detail"])


if __name__ == "__main__":
    unittest.main()

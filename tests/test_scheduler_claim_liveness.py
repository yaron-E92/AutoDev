from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import claim_contract, queue_selection, scheduler


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


def make_worker(root: Path) -> Path:
    origin = root / "origin.git"
    seed = root / "seed"
    worker = root / "worker"
    git(root, "init", "--bare", str(origin))
    seed.mkdir()
    git(seed, "init")
    git(seed, "config", "user.name", "Test")
    git(seed, "config", "user.email", "test@example.invalid")
    (seed / "README.md").write_text("scheduler liveness\n", encoding="utf-8")
    git(seed, "add", "README.md")
    git(seed, "commit", "-m", "seed")
    git(seed, "branch", "-M", "main")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-u", "origin", "main")
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    git(root, "clone", str(origin), str(worker))

    current = worker / ".autodev-run" / "current"
    current.mkdir(parents=True)
    (current / "state.json").write_text(
        json.dumps(
            {
                "Status": "Prepared",
                "IssueNumber": 42,
                "BranchName": "autodev/issue-42-work",
                "Base": "main",
                "BaseSha": "a" * 40,
                "LastCommitSha": "",
                "PrNumber": 0,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return worker


def registration(root: Path, worker: Path) -> Path:
    path = root / "scheduler" / scheduler.REGISTRATION_FILE
    value = scheduler.SchedulerRegistration(
        github_repository="owner/repo",
        source_repository=str(root / "source"),
        worker_repository=str(worker),
        default_branch="main",
        backend=scheduler.BACKEND_CRON,
        cadence_minutes=15,
        launcher=str(root / "autodev"),
        task_id="autodev-owner-repo",
        installed_at="2026-09-05T12:00:00Z",
    )
    scheduler._write_registration(path, value)
    return path


class DummyLease:
    def __init__(self, _repo: Path, claim: claim_contract.Claim, **_kwargs):
        self.claim = claim
        self.lost = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def latest_claim(self):
        return self.claim


class SchedulerClaimLivenessTests(unittest.TestCase):
    def test_repeated_unchanged_scheduler_ticks_eventually_persist_run_stalled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worker = make_worker(root)
            registration_file = registration(root, worker)
            policy = claim_contract.ClaimPolicy(
                lease_minutes=120,
                max_no_progress_attempts=2,
                max_no_progress_minutes=360,
            )
            selected = queue_selection.SelectionResult(
                state="SELECTED",
                repository="owner/repo",
                issue_number=42,
                explanation="selected",
            )
            resumed = queue_selection.SelectionResult(
                state="RESUME_EXISTING",
                repository="owner/repo",
                issue_number=42,
                source="existing-run",
                explanation="resume unchanged run",
            )
            existing_none = queue_selection.ExistingRun("NONE")
            existing_resume = queue_selection.ExistingRun(
                "RESUME_EXISTING",
                issue_number=42,
                branch="autodev/issue-42-work",
                next_stage="local-check",
                next_action="resume",
            )
            outputs = [io.StringIO(), io.StringIO(), io.StringIO()]
            errors = [io.StringIO(), io.StringIO(), io.StringIO()]

            with patch.object(
                scheduler,
                "_prepare_worker",
                side_effect=[existing_none, existing_resume, existing_resume],
            ), patch.object(
                scheduler.claim_identity,
                "load_claim_policy",
                return_value=policy,
            ), patch.object(
                scheduler.claim_identity,
                "worker_identity",
                return_value=claim_contract.WorkerIdentity("worker-a"),
            ), patch.object(
                scheduler.claim_recovery,
                "reconcile_stale_claims",
            ), patch.object(
                scheduler.queue_selection,
                "select_next",
                side_effect=[selected, resumed, resumed],
            ), patch.object(
                scheduler.scheduler_runtime_worker,
                "validate_worker",
            ), patch.object(
                scheduler.claim_lease,
                "HeartbeatLease",
                DummyLease,
            ), patch.object(
                scheduler,
                "_coordinator_state",
                return_value="Prepared",
            ):
                codes = [
                    scheduler.run_cli(
                        ["run-once", "--registration", str(registration_file)],
                        coordinator=lambda _argv: 0,
                        stdout=outputs[index],
                        stderr=errors[index],
                    )
                    for index in range(3)
                ]

            self.assertEqual(codes, [0, 0, 2])
            self.assertEqual(json.loads(outputs[0].getvalue())["state"], "DISPATCHED")
            self.assertEqual(json.loads(outputs[1].getvalue())["state"], "DISPATCHED")
            self.assertIn("RUN_STALLED issue #42", errors[2].getvalue())
            self.assertIn("no-progress attempts=2/2", errors[2].getvalue())
            self.assertIn("autodev resume", errors[2].getvalue())

            latest = scheduler._load_registration(registration_file)
            self.assertIsNotNone(latest)
            self.assertEqual(latest.last_run["state"], "SCHEDULER_ERROR")
            self.assertIn("RUN_STALLED issue #42", latest.last_run["detail"])


if __name__ == "__main__":
    unittest.main()

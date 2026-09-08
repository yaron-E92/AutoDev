from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import continuation, continuation_recovery, run_manifest, workflow_storage, workflow_workspace
from tests.test_continuation import ContinuationGitFixture


class ContinuationRecoveryTests(unittest.TestCase):
    def _durable_run(self, repo: Path, base: str) -> Path:
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        issue = current / "issue.md"
        issue.write_text("# Issue 293\n", encoding="utf-8")
        workflow_workspace.write_workspace_snapshot(repo, current / "workspace-snapshot.json")
        state = {
            "Status": "Prepared",
            "RepoFullName": "example/project",
            "IssueNumber": 293,
            "IssueTitle": "Continue from",
            "Base": "develop",
            "BaseSha": base,
            "BaseTreeSha": subprocess.run(
                ["git", "rev-parse", f"{base}^{{tree}}"],
                cwd=repo,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip(),
            "BranchName": "autodev/issue-293",
            "PreparedLocalHeadSha": base,
            "PreparedSnapshotHash": workflow_storage._file_sha256(
                current / "workspace-snapshot.json"
            ),
            "AcceptedRoleArtifacts": {},
        }
        workflow_storage.write_json(current / "state.json", state)
        run_manifest.create_manifest(
            current / run_manifest.MANIFEST_NAME,
            repo_path=repo,
            github_repo="example/project",
            issue_number=293,
            mode="issue-to-pr",
            base_sha=base,
            branch="autodev/issue-293",
            role_snapshots={},
        )
        run_manifest.complete_stage(
            current / run_manifest.MANIFEST_NAME,
            "issue-selected",
            run_root=current,
            artifacts=[issue],
        )
        return current

    def test_full_commit_sha_is_a_valid_immutable_continuation_ref(self):
        fixture = ContinuationGitFixture()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _, feature = fixture._repo(repo)
            self.assertEqual(continuation.resolve_ref(repo, feature), feature)

    def test_invalid_ref_fails_before_any_pending_transaction_is_written(self):
        fixture = ContinuationGitFixture()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base, _ = fixture._repo(repo)
            current = self._durable_run(repo, base)

            with self.assertRaises(continuation.ContinuationError):
                continuation_recovery.adopt(repo, "definitely/missing")

            self.assertFalse((current / continuation_recovery.PENDING_FILE).exists())

    def test_interruption_after_pending_write_is_finished_by_plain_resume_boundary(self):
        fixture = ContinuationGitFixture()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base, feature = fixture._repo(repo)
            current = self._durable_run(repo, base)

            # Simulate process loss immediately after the transaction boundary
            # is durable, before checkout/adoption has a chance to run.
            with patch.object(
                continuation_recovery,
                "finish_pending",
                side_effect=KeyboardInterrupt("simulated process loss"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    continuation_recovery.adopt(repo, "feature/existing-work")

            pending = json.loads(
                (current / continuation_recovery.PENDING_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(pending["requested_ref"], "feature/existing-work")
            self.assertEqual(pending["resolved_sha"], feature)

            # Move the original branch after the interruption. Recovery must use
            # the persisted SHA, not follow the moved branch.
            subprocess.run(
                ["git", "branch", "-f", "feature/existing-work", "develop"],
                cwd=repo,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            )

            recovered = continuation_recovery.finish_pending(repo)
            self.assertEqual(recovered["requested_ref"], "feature/existing-work")
            self.assertEqual(recovered["resolved_sha"], feature)
            self.assertFalse((current / continuation_recovery.PENDING_FILE).exists())

            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(head, feature)
            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["ContinuationRequestedRef"], "feature/existing-work")
            self.assertEqual(state["ContinuationResolvedSha"], feature)

    def test_already_completed_pending_transaction_is_idempotently_cleared(self):
        fixture = ContinuationGitFixture()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base, feature = fixture._repo(repo)
            current = self._durable_run(repo, base)
            continuation_recovery.adopt(repo, "feature/existing-work")

            # Recreate the same pending marker to model a crash after the core
            # adoption committed durable state but before pending-file cleanup.
            workflow_storage.write_json(
                current / continuation_recovery.PENDING_FILE,
                {
                    "schema_version": continuation.SCHEMA_VERSION,
                    "requested_ref": "feature/existing-work",
                    "resolved_sha": feature,
                },
            )
            before_archive = str(
                json.loads((current / "state.json").read_text(encoding="utf-8")).get(
                    "ContinuationArchive", ""
                )
            )
            recovered = continuation_recovery.finish_pending(repo)
            after_state = json.loads((current / "state.json").read_text(encoding="utf-8"))

            self.assertEqual(recovered["resolved_sha"], feature)
            self.assertEqual(after_state["ContinuationArchive"], before_archive)
            self.assertFalse((current / continuation_recovery.PENDING_FILE).exists())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import opencode_runtime, workflow_stages


class OpenCodeShippedPrAndCiTests(unittest.TestCase):
    def setUp(self) -> None:
        opencode_runtime.install_workflow_guards()

    def test_shipped_commit_recovers_pr_before_no_changes_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            source = repo / "source.txt"
            source.write_text("verified\n", encoding="utf-8")
            snapshot = current / "last-commit-workspace-snapshot.json"
            workflow_stages.write_workspace_snapshot(repo, snapshot)
            snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            digest = hashlib.sha256(source.read_bytes()).hexdigest().upper()
            state: dict[str, object] = {
                "RepoFullName": "owner/repo",
                "IssueNumber": 102,
                "IssueTitle": "Issue 102",
                "BranchName": "autodev/issue-102",
                "Base": "main",
                "BaseSha": "base-sha",
                "LastCommitSha": "commit-sha",
                "CreatedCommitSha": "commit-sha",
                "CreatedTreeSha": "tree-sha",
                "CreatedParentSha": "base-sha",
                "ShippedSourceIdentity": "verified-identity",
                "ShippedTreeVerified": True,
                "VerifiedParentSha": "base-sha",
                "VerifiedSourceIdentity": "verified-identity",
                "VerifiedChanges": [
                    {"path": "source.txt", "status": "modified", "sha256": digest}
                ],
                "LastCommitSnapshotHash": snapshot_hash,
                "VerificationProofVersion": 1,
                "LastLocalCheckPassed": True,
                "LastSemanticVerdict": "pass",
                "SemanticSourceIdentity": "verified-identity",
                "LocalCheck": "check",
                "PrUrl": "",
                "PrNumber": 0,
                "PrHeadSha": "",
            }
            workflow_stages.write_state(current, state)

            pr = {
                "number": 52,
                "html_url": "https://github.com/owner/repo/pull/52",
                "head": {
                    "sha": "commit-sha",
                    "ref": "autodev/issue-102",
                    "repo": {"full_name": "owner/repo"},
                },
            }

            def runner(command, **kwargs):
                if command[0:2] == ["gh", "api"]:
                    return SimpleNamespace(returncode=0, stdout=json.dumps([pr]), stderr="")
                if command[0:3] == ["gh", "pr", "view"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps({"number": 52, "headRefOid": "commit-sha"}),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {command}")

            ci = {
                "head_sha": "commit-sha",
                "state": "terminal-success",
                "checks": [{"name": "build", "bucket": "pass", "state": "SUCCESS"}],
                "polls": 1,
                "required_only": True,
            }
            with (
                patch.object(workflow_stages, "wait_for_required_checks", return_value=ci),
            ):
                passed = workflow_stages.pr_and_ci(
                    repo,
                    current,
                    workflow_stages.read_state(current),
                    Path(temp_dir),
                    runner=runner,
                )

            self.assertTrue(passed)
            recovered = workflow_stages.read_state(current)
            self.assertEqual(recovered["PrNumber"], 52)
            self.assertEqual(recovered["PrHeadSha"], "commit-sha")
            self.assertEqual(recovered["CiProof"]["state"], "terminal-success")


if __name__ == "__main__":
    unittest.main()

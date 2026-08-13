from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import opencode_resume, opencode_runtime, workflow_stages


class OpenCodePostShipmentIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        opencode_runtime.install_workflow_guards()

    def _state(self, repo: Path) -> tuple[Path, dict[str, object]]:
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
            "PrUrl": "",
            "PrNumber": 0,
            "PrHeadSha": "",
        }
        workflow_stages.write_state(current, state)
        return current, state

    def test_unchanged_workspace_keeps_pre_shipment_verified_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state = self._state(repo)

            proof = workflow_stages.source_identity(repo, current, state)

            self.assertEqual(proof["parent_sha"], "base-sha")
            self.assertEqual(proof["identity"], "verified-identity")
            self.assertEqual(proof["changes"], state["VerifiedChanges"])

            (repo / "source.txt").write_text("drift\n", encoding="utf-8")
            drift = workflow_stages.source_identity(repo, current, workflow_stages.read_state(current))
            self.assertEqual(drift["parent_sha"], "commit-sha")
            self.assertNotEqual(drift["identity"], "verified-identity")
            self.assertTrue(drift["changes"])

    def test_patch_checkpoint_accepts_intentional_local_remote_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state = self._state(repo)
            manifest = {
                "target": {
                    "repo_path": str(repo.resolve()),
                    "github_repo": "owner/repo",
                    "issue_number": 102,
                    "base_sha": "base-sha",
                    "branch": "autodev/issue-102",
                },
                "stages": {
                    "patch-applied": {
                        "status": "completed",
                        "details": {"source_identity": "verified-identity"},
                    }
                },
            }

            def runner(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout="base-sha\n", stderr="")

            with (
                patch.object(opencode_resume.run_manifest, "validate_artifacts", return_value=[]),
                patch.object(
                    opencode_resume.run_manifest,
                    "stage_completed",
                    side_effect=lambda _manifest, stage: stage == "patch-applied",
                ),
            ):
                problems = opencode_resume._resume_problems(
                    repo,
                    current,
                    manifest,
                    state,
                    runner=runner,
                    validate_remote=False,
                )

            self.assertEqual(problems, [])

            (repo / "source.txt").write_text("real drift\n", encoding="utf-8")
            with (
                patch.object(opencode_resume.run_manifest, "validate_artifacts", return_value=[]),
                patch.object(
                    opencode_resume.run_manifest,
                    "stage_completed",
                    side_effect=lambda _manifest, stage: stage == "patch-applied",
                ),
            ):
                problems = opencode_resume._resume_problems(
                    repo,
                    current,
                    manifest,
                    workflow_stages.read_state(current),
                    runner=runner,
                    validate_remote=False,
                )
            self.assertIn("source/worktree drift detected after the patch-applied checkpoint", problems)


if __name__ == "__main__":
    unittest.main()

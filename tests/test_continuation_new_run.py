from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import continuation, run_manifest, workflow_preparation, workflow_storage, workflow_workspace
from tests.test_continuation import ContinuationGitFixture


class NewRunContinuationTests(unittest.TestCase):
    def test_new_run_uses_continuation_sha_as_source_without_changing_policy_base(self):
        fixture = ContinuationGitFixture()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base, feature = fixture._repo(repo)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)

            continuation.install_hooks()
            with patch.dict(
                os.environ,
                {
                    continuation.CONTINUE_FROM_ENV: "feature/existing-work",
                    continuation.CONTINUE_FROM_SHA_ENV: feature,
                },
                clear=False,
            ):
                prepared_head = workflow_preparation.validate_prepared_worktree(
                    repo,
                    base,
                )
                self.assertEqual(prepared_head, feature)
                actual_head = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=repo,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=True,
                ).stdout.strip()
                self.assertEqual(actual_head, feature)

                workflow_workspace.write_workspace_snapshot(
                    repo,
                    current / "workspace-snapshot.json",
                )
                state = {
                    "Base": "develop",
                    "BaseSha": base,
                    "BranchName": "autodev/issue-293-new-run",
                    "PreparedLocalHeadSha": feature,
                    "PreparedSnapshotHash": workflow_storage._file_sha256(
                        current / "workspace-snapshot.json"
                    ),
                    "LastCommitSha": "",
                }
                workflow_storage.write_json(current / "state.json", state)
                continuation._bind_new_run(repo, current)

            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["Base"], "develop")
            self.assertEqual(state["BaseSha"], base)
            self.assertEqual(state["ContinuationRequestedRef"], "feature/existing-work")
            self.assertEqual(state["ContinuationResolvedSha"], feature)

            source = workflow_workspace.source_identity(repo, current, state)
            self.assertEqual(source["parent_sha"], feature)
            changed_paths = {
                str(change.get("path", ""))
                for change in source["changes"]
                if isinstance(change, dict)
            }
            self.assertNotIn("app.txt", changed_paths)

            manifest_path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                manifest_path,
                repo_path=repo,
                github_repo="example/project",
                issue_number=293,
                mode="issue-to-pr",
                base_sha=base,
                branch="autodev/issue-293-new-run",
                role_snapshots={},
            )
            continuation._bind_manifest(repo, manifest_path)
            manifest = run_manifest.load_manifest(manifest_path)

            self.assertEqual(manifest["target"]["base_sha"], base)
            self.assertEqual(manifest["target"]["branch"], "autodev/issue-293-new-run")
            self.assertEqual(manifest["continuation_source"]["requested_ref"], "feature/existing-work")
            self.assertEqual(manifest["continuation_source"]["resolved_sha"], feature)


if __name__ == "__main__":
    unittest.main()

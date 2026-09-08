from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import continuation, run_manifest, workflow_storage, workflow_workspace


class ContinuationGitFixture:
    def _git(self, repo: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=True,
        )
        return completed.stdout.strip()

    def _repo(self, root: Path) -> tuple[str, str]:
        self._git(root, "init", "-b", "develop")
        self._git(root, "config", "user.email", "autodev@example.invalid")
        self._git(root, "config", "user.name", "AutoDev Tests")
        (root / "app.txt").write_text("base\n", encoding="utf-8")
        self._git(root, "add", "app.txt")
        self._git(root, "commit", "-m", "base")
        base = self._git(root, "rev-parse", "HEAD")

        self._git(root, "checkout", "-b", "feature/existing-work")
        (root / "app.txt").write_text("existing work\n", encoding="utf-8")
        self._git(root, "commit", "-am", "existing work")
        feature = self._git(root, "rev-parse", "HEAD")
        self._git(root, "checkout", "develop")
        return base, feature


class ContinuationTests(ContinuationGitFixture, unittest.TestCase):
    def test_cli_continue_from_is_removed_without_becoming_base_override(self):
        values, requested, error = continuation.consume_public_args(
            [
                "issue-to-pr",
                "182",
                "--continue-from",
                "feature/existing-work",
                "--repo",
                ".",
            ]
        )
        self.assertEqual(error, "")
        self.assertEqual(requested, "feature/existing-work")
        self.assertEqual(values, ["issue-to-pr", "182", "--repo", "."])

    def test_ref_is_resolved_once_even_if_branch_moves_after_scope_starts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _, feature = self._repo(repo)
            old_ref = os.environ.get(continuation.CONTINUE_FROM_ENV)
            old_sha = os.environ.get(continuation.CONTINUE_FROM_SHA_ENV)
            with continuation.new_run_scope(repo, "feature/existing-work") as resolved:
                self.assertEqual(resolved, feature)
                self.assertEqual(os.environ[continuation.CONTINUE_FROM_SHA_ENV], feature)
                self._git(repo, "branch", "-f", "feature/existing-work", "develop")
                self.assertEqual(os.environ[continuation.CONTINUE_FROM_SHA_ENV], feature)
            self.assertEqual(os.environ.get(continuation.CONTINUE_FROM_ENV), old_ref)
            self.assertEqual(os.environ.get(continuation.CONTINUE_FROM_SHA_ENV), old_sha)

    def test_unrelated_continuation_is_rejected_instead_of_retargeting_git_flow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base, _ = self._repo(repo)
            self._git(repo, "checkout", "--orphan", "unrelated")
            self._git(repo, "rm", "-rf", ".")
            (repo / "other.txt").write_text("unrelated\n", encoding="utf-8")
            self._git(repo, "add", "other.txt")
            self._git(repo, "commit", "-m", "unrelated")
            unrelated = self._git(repo, "rev-parse", "HEAD")

            with self.assertRaises(continuation.ContinuationError) as raised:
                continuation._require_policy_ancestry(repo, base, unrelated)
            self.assertIn("configured development base", str(raised.exception))

    def test_existing_run_adoption_archives_history_and_keeps_policy_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            base, feature = self._repo(repo)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            issue = current / "issue.md"
            reader = current / "reader-brief.md"
            synthesis = current / "synthesized-handoff.md"
            plan = current / "plan.md"
            issue.write_text("# Issue\n", encoding="utf-8")
            reader.write_text("reader\n", encoding="utf-8")
            synthesis.write_text("synthesis\n", encoding="utf-8")
            plan.write_text("plan\n", encoding="utf-8")
            workflow_workspace.write_workspace_snapshot(repo, current / "workspace-snapshot.json")
            snapshot_hash = workflow_storage._file_sha256(current / "workspace-snapshot.json")
            state = {
                "Status": "ReadyForReview",
                "RepoFullName": "example/project",
                "IssueNumber": 293,
                "IssueTitle": "Continue from",
                "Base": "develop",
                "BaseSha": base,
                "BaseTreeSha": self._git(repo, "rev-parse", f"{base}^{{tree}}"),
                "BranchName": "autodev/issue-293-old",
                "PreparedLocalHeadSha": base,
                "PreparedSnapshotHash": snapshot_hash,
                "LastCommitSha": "old-commit",
                "LastLocalCheckPassed": True,
                "PrUrl": "https://github.com/example/project/pull/1",
                "PrNumber": 1,
                "PrHeadSha": "old-head",
                "CiProof": {"state": "terminal-success"},
                "AcceptedRoleArtifacts": {
                    role: {"artifact": f"{role}.out", "sha256": "old"}
                    for role in ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
                },
            }
            workflow_storage.write_json(current / "state.json", state)
            manifest_path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                manifest_path,
                repo_path=repo,
                github_repo="example/project",
                issue_number=293,
                mode="issue-to-pr",
                base_sha=base,
                branch="autodev/issue-293-old",
                role_snapshots={
                    role: {"fingerprint": f"fp-{role}", "safe_metadata": {}}
                    for role in ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
                },
            )
            run_manifest.complete_stage(
                manifest_path,
                "issue-selected",
                run_root=current,
                artifacts=[issue],
            )
            run_manifest.complete_stage(
                manifest_path,
                "repository-read",
                run_root=current,
                artifacts=[reader],
            )
            run_manifest.complete_stage(
                manifest_path,
                "handoff-synthesized",
                run_root=current,
                artifacts=[synthesis],
            )
            run_manifest.complete_stage(
                manifest_path,
                "plan-created",
                run_root=current,
                artifacts=[plan],
            )

            record = continuation.adopt_existing_run(repo, "feature/existing-work")

            self.assertEqual(record["resolved_sha"], feature)
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), feature)
            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["Base"], "develop")
            self.assertEqual(state["BaseSha"], base)
            self.assertEqual(state["ContinuationRequestedRef"], "feature/existing-work")
            self.assertEqual(state["ContinuationResolvedSha"], feature)
            self.assertEqual(state["PreparedLocalHeadSha"], feature)
            self.assertEqual(state["PrUrl"], "")
            self.assertEqual(state["PrNumber"], 0)
            self.assertEqual(state["AcceptedRoleArtifacts"], {})
            self.assertIn("-continuation-", state["BranchName"])
            archive = current / state["ContinuationArchive"]
            self.assertTrue((archive / "state-before.json").is_file())
            self.assertTrue((archive / "run-manifest-before.json").is_file())

            manifest = run_manifest.load_manifest(manifest_path)
            self.assertEqual(manifest["target"]["base_sha"], base)
            self.assertEqual(manifest["target"]["branch"], state["BranchName"])
            self.assertEqual(manifest["continuation_source"]["resolved_sha"], feature)
            self.assertEqual(manifest["completed_stages"], ["issue-selected"])

    def test_help_exposes_continue_from_on_both_public_workflows(self):
        continuation.register_help()
        for command in ("issue-to-pr", "resume"):
            entry = continuation.cli_help.HELP[(command,)]
            self.assertIn("--continue-from REF", entry.usage)
            self.assertIn("--continue-from REF", {name for name, _ in entry.options})


if __name__ == "__main__":
    unittest.main()

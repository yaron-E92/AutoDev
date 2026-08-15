from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import workflow_stages


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


class GitExclusionWorkflowScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "autodev@example.invalid")
        _git(self.repo, "config", "user.name", "AutoDev Tests")
        (self.repo / "tracked.cs").write_text("before\n", encoding="utf-8")
        _git(self.repo, "add", "tracked.cs")
        _git(self.repo, "commit", "-m", "base")
        self.base_sha = _git(self.repo, "rev-parse", "HEAD")
        # Real installed target repositories exclude AutoDev's generated run
        # directory.  The Git-backed scope intentionally relies on that Git
        # policy rather than silently hardcoding runtime paths in Git mode.
        self._exclude(".autodev-run/\n")
        self.current = self.repo / workflow_stages.CURRENT_DIR
        self.current.mkdir(parents=True)
        self.snapshot = self.current / "workspace-snapshot.json"
        workflow_stages.write_workspace_snapshot(self.repo, self.snapshot)
        self.state = {"BaseSha": self.base_sha}

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _exclude(self, text: str) -> None:
        value = Path(_git(self.repo, "rev-parse", "--git-path", "info/exclude"))
        if not value.is_absolute():
            value = self.repo / value
        value.parent.mkdir(parents=True, exist_ok=True)
        existing = value.read_text(encoding="utf-8") if value.is_file() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        value.write_text(existing + text, encoding="utf-8")

    def test_secondbrain_regression_local_excludes_do_not_change_identity_or_changes(self):
        before = workflow_stages.source_identity(self.repo, self.current, self.state)
        self._exclude(".autodev/\n.serena/\nopencode.jsonc\n")
        (self.repo / ".autodev").mkdir()
        (self.repo / ".autodev" / "privacy.json").write_text("{}\n", encoding="utf-8")
        (self.repo / ".serena" / "memories").mkdir(parents=True)
        (self.repo / ".serena" / "memories" / "memory_maintenance.md").write_text(
            "local\n", encoding="utf-8"
        )
        (self.repo / "opencode.jsonc").write_text("{}\n", encoding="utf-8")

        after = workflow_stages.source_identity(self.repo, self.current, self.state)

        self.assertEqual(after["identity"], before["identity"])
        self.assertEqual(after["changes"], [])
        self.assertEqual(workflow_stages.workspace_changes(self.repo, self.current, self.state), [])

    def test_nonignored_untracked_file_is_identity_and_shipment_change(self):
        (self.repo / "new source.cs").write_text("new\n", encoding="utf-8")

        proof = workflow_stages.source_identity(self.repo, self.current, self.state)
        changes = workflow_stages.workspace_changes(self.repo, self.current, self.state)

        self.assertEqual(
            proof["changes"],
            [
                {
                    "path": "new source.cs",
                    "status": "added",
                    "sha256": workflow_stages.workspace_snapshot(self.repo)["new source.cs"],
                }
            ],
        )
        self.assertEqual(changes, [{"Path": "new source.cs", "Status": "added"}])

    def test_tracked_file_is_still_identity_change_after_ignore_rule_matches(self):
        (self.repo / ".gitignore").write_text("tracked.cs\n", encoding="utf-8")
        (self.repo / "tracked.cs").write_text("after\n", encoding="utf-8")

        changes = workflow_stages.workspace_changes(self.repo, self.current, self.state)

        self.assertIn({"Path": "tracked.cs", "Status": "modified"}, changes)

    def test_deleted_tracked_file_is_reported(self):
        (self.repo / "tracked.cs").unlink()

        proof = workflow_stages.source_identity(self.repo, self.current, self.state)
        changes = workflow_stages.workspace_changes(self.repo, self.current, self.state)

        self.assertIn({"path": "tracked.cs", "status": "deleted", "sha256": ""}, proof["changes"])
        self.assertIn({"Path": "tracked.cs", "Status": "deleted"}, changes)

    def test_api_commit_refuses_manually_supplied_ignored_untracked_path_before_upload(self):
        self._exclude("local-secret.json\n")
        (self.repo / "local-secret.json").write_text('{"secret": true}\n', encoding="utf-8")
        state = {
            "RepoFullName": "example/repo",
            "BranchName": "autodev/test",
            "BaseSha": self.base_sha,
        }

        with self.assertRaisesRegex(
            workflow_stages.WorkflowStageError,
            "outside Git's tracked/nonignored workspace scope",
        ):
            workflow_stages.create_api_commit(
                self.repo,
                state,
                [{"Path": "local-secret.json", "Status": "added"}],
                self.current,
            )


if __name__ == "__main__":
    unittest.main()

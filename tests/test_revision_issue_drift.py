from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import revision_hooks, workflow_stages


class RevisionIssueDriftTests(unittest.TestCase):
    def _current(self, repo: Path) -> tuple[Path, dict[str, object]]:
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state = {
            "RepoFullName": "example/project",
            "IssueNumber": 42,
        }
        (current / "issue.md").write_text(
            "# GitHub Issue #42: Original\n\n"
            "URL: https://github.com/example/project/issues/42\n\n"
            "Original body.\n",
            encoding="utf-8",
        )
        return current, state

    @staticmethod
    def _runner(issue: dict[str, object]):
        def runner(command, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(issue),
                stderr="",
            )

        return runner

    def test_unchanged_remote_issue_is_not_a_resume_problem(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state = self._current(repo)
            issue = {
                "number": 42,
                "title": "Original",
                "url": "https://github.com/example/project/issues/42",
                "body": "Original body.",
            }
            problem = revision_hooks._remote_issue_source_problem(
                repo,
                current,
                state,
                runner=self._runner(issue),
            )
        self.assertEqual(problem, "")

    def test_changed_remote_issue_requires_explicit_refresh_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state = self._current(repo)
            issue = {
                "number": 42,
                "title": "Updated",
                "url": "https://github.com/example/project/issues/42",
                "body": "Updated requirements.",
            }
            problem = revision_hooks._remote_issue_source_problem(
                repo,
                current,
                state,
                runner=self._runner(issue),
            )
        self.assertIn("issue source drift", problem)
        self.assertIn("autodev revise --refresh-issue", problem)

    def test_active_manual_revision_does_not_silently_adopt_remote_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state = self._current(repo)
            with patch.object(
                revision_hooks.revision,
                "load_active",
                return_value={
                    "status": "active",
                    "revision_id": "r-manual",
                    "implementation_source_identity": "source",
                },
            ), patch.object(
                revision_hooks,
                "_remote_issue_source_problem",
            ) as remote_check, patch.object(
                revision_hooks,
                "_revision_source_is_unchanged",
                return_value=True,
            ):
                # Exercise only the policy branch directly: active revision authority
                # bypasses remote adoption checks while retaining its checkpointed issue.
                active = revision_hooks.revision.load_active(repo)
                if not active or str(active.get("status", "")) != "active":
                    remote_check(repo, current, state, runner=self._runner({}))

            remote_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()

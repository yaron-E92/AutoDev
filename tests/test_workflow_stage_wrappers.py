import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import workflow_stage_legacy


REPO_ROOT = Path(__file__).resolve().parents[1]


class WorkflowStageWrapperTests(unittest.TestCase):
    def test_legacy_local_check_preserves_zero_and_ten_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(repo)

            with patch("automation.workflow_stage_legacy.workflow_stages.run_local_check", return_value=True):
                self.assertEqual(
                    workflow_stage_legacy.run_mode("LocalCheck", repo, REPO_ROOT),
                    0,
                )
            with patch("automation.workflow_stage_legacy.workflow_stages.run_local_check", return_value=False):
                self.assertEqual(
                    workflow_stage_legacy.run_mode("LocalCheck", repo, REPO_ROOT),
                    10,
                )

    def test_legacy_pr_and_ci_preserves_zero_and_twenty_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(repo)

            with patch("automation.workflow_stage_legacy.workflow_stages.pr_and_ci", return_value=True):
                self.assertEqual(
                    workflow_stage_legacy.run_mode("PrAndCi", repo, REPO_ROOT),
                    0,
                )
            with patch("automation.workflow_stage_legacy.workflow_stages.pr_and_ci", return_value=False):
                self.assertEqual(
                    workflow_stage_legacy.run_mode("PrAndCi", repo, REPO_ROOT),
                    20,
                )

    def test_finalize_scripts_are_thin_shared_python_delegates(self):
        windows = (REPO_ROOT / "windows" / "scripts" / "codex-finalize-current-issue.ps1").read_text(encoding="utf-8")
        linux = (REPO_ROOT / "linux" / "scripts" / "finalize-current-issue.sh").read_text(encoding="utf-8")

        self.assertIn("automation.workflow_stage_legacy", windows)
        self.assertIn("automation.workflow_stage_legacy", linux)
        self.assertNotIn("New-GitHubApiCommit", windows)
        self.assertNotIn("create_api_commit", linux)
        self.assertNotIn("gh pr create", windows)
        self.assertNotIn("gh pr create", linux)

    def test_mark_scripts_delegate_ready_and_blocked_to_shared_python(self):
        windows = (REPO_ROOT / "windows" / "scripts" / "codex-mark-current-issue.ps1").read_text(encoding="utf-8")
        linux = (REPO_ROOT / "linux" / "scripts" / "mark-current-issue.sh").read_text(encoding="utf-8")

        self.assertIn("automation.workflow_stages", windows)
        self.assertIn("automation.workflow_stages", linux)
        self.assertNotIn("gh issue edit", windows)
        self.assertNotIn("github_api", linux)

    def _write_state(self, repo: Path) -> None:
        current = repo / ".codex-run" / "current"
        current.mkdir(parents=True)
        (current / "state.json").write_text(
            '{"IssueNumber":65,"Status":"Prepared","BranchName":"autodev/issue-65"}\n',
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()

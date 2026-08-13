from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import opencode_runtime, workflow_stages


class OpenCodePrRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        opencode_runtime.install_workflow_guards()

    @staticmethod
    def _pr(sha: str = "commit-sha") -> dict[str, object]:
        return {
            "number": 52,
            "html_url": "https://github.com/owner/repo/pull/52",
            "head": {
                "sha": sha,
                "ref": "autodev/issue-102",
                "repo": {"full_name": "owner/repo"},
            },
        }

    @staticmethod
    def _state(current: Path) -> None:
        workflow_stages.write_state(
            current,
            {
                "RepoFullName": "owner/repo",
                "BranchName": "autodev/issue-102",
                "Base": "main",
                "BaseSha": "base-sha",
                "LastCommitSha": "commit-sha",
                "VerificationProofVersion": 1,
                "PrUrl": "",
                "PrNumber": 0,
                "PrHeadSha": "",
            },
        )

    def test_existing_exact_branch_pr_is_recovered_without_create(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            self._state(current)
            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return SimpleNamespace(returncode=0, stdout=json.dumps([self._pr()]), stderr="")

            workflow_stages.ensure_pr(repo, current, workflow_stages.read_state(current), runner=runner)

            state = workflow_stages.read_state(current)
            self.assertEqual(state["PrNumber"], 52)
            self.assertEqual(state["PrHeadSha"], "commit-sha")
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][0:2], ["gh", "api"])

    def test_failed_pr_create_recovers_remote_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            self._state(current)
            state = workflow_stages.read_state(current)
            state["IssueTitle"] = "Issue 102"
            workflow_stages.write_state(current, state)
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")
            (current / "plan.md").write_text("Plan\n", encoding="utf-8")
            responses = [
                SimpleNamespace(returncode=0, stdout="[]", stderr=""),
                SimpleNamespace(returncode=1, stdout="", stderr='no pull requests found for branch ""'),
                SimpleNamespace(returncode=0, stdout=json.dumps([self._pr()]), stderr=""),
            ]
            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return responses.pop(0)

            workflow_stages.ensure_pr(repo, current, workflow_stages.read_state(current), runner=runner)

            state = workflow_stages.read_state(current)
            self.assertEqual(state["PrNumber"], 52)
            self.assertTrue(any(command[1:3] == ["pr", "create"] for command in commands))

    def test_failed_pr_create_without_remote_match_keeps_original_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            self._state(current)
            (current / "issue.md").write_text("# Issue\n", encoding="utf-8")
            (current / "plan.md").write_text("Plan\n", encoding="utf-8")
            responses = [
                SimpleNamespace(returncode=0, stdout="[]", stderr=""),
                SimpleNamespace(returncode=1, stdout="", stderr="creation exploded"),
                SimpleNamespace(returncode=0, stdout="[]", stderr=""),
            ]

            def runner(command, **kwargs):
                return responses.pop(0)

            with self.assertRaises(workflow_stages.WorkflowStageError) as raised:
                workflow_stages.ensure_pr(repo, current, workflow_stages.read_state(current), runner=runner)

            self.assertIn("creation exploded", str(raised.exception))

    def test_recovered_pr_with_wrong_head_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            self._state(current)

            def runner(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout=json.dumps([self._pr("other")]), stderr="")

            with self.assertRaises(workflow_stages.WorkflowStageError) as raised:
                workflow_stages.ensure_pr(repo, current, workflow_stages.read_state(current), runner=runner)

            self.assertIn("does not match exact AutoDev commit", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

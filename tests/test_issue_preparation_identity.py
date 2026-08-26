from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import workflow_preparation
from automation.workflow_contract import CURRENT_DIR, WorkflowStageError


REPO_ROOT = Path(__file__).resolve().parents[1]


class IssuePreparationIdentityTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "target"
        repo.mkdir()
        (repo / ".git").mkdir()
        return repo

    def _remote_runner(self, repo: Path, calls: list[tuple[list[str], Path]]):
        def runner(argv, **kwargs):
            args = list(argv)
            cwd = Path(kwargs["cwd"])
            calls.append((args, cwd))
            if args == ["git", "remote", "get-url", "--all", "origin"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="https://github.com/Tax-Technology/goldilocks.git\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {args}")

        return runner

    def _github_evidence(self):
        return (
            {
                "number": 6,
                "title": "Create decision spaces",
                "body": "Implement persistent decision spaces.",
                "url": "https://github.com/Tax-Technology/goldilocks/issues/6",
                "labels": [],
            },
            {"object": {"sha": "base-sha"}},
            {"tree": {"sha": "tree-sha"}},
        )

    @staticmethod
    def _snapshot(_repo: Path, path: Path) -> None:
        path.write_text("{}\n", encoding="utf-8")

    def test_new_issue_prepares_from_target_https_remote_without_identity_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            caller = root / "caller"
            caller.mkdir()
            repo = self._repo(root)
            calls: list[tuple[list[str], Path]] = []
            issue, base_ref, base_commit = self._github_evidence()

            with (
                patch.dict(
                    os.environ,
                    {"LOCAL_CHECK": "python -m unittest", "STACK_CONTEXT": "Python"},
                    clear=True,
                ),
                patch("automation.workflow_preparation.gh_json", side_effect=[issue, base_ref, base_commit]) as gh_json,
                patch("automation.workflow_preparation.validate_prepared_worktree", return_value="base-sha"),
                patch("automation.workflow_preparation.write_workspace_snapshot", side_effect=self._snapshot),
                patch("automation.workflow_preparation.gh") as gh,
                patch.object(Path, "cwd", return_value=caller),
            ):
                current = workflow_preparation.ensure_prepared_issue(
                    repo,
                    "6",
                    autodev_root=REPO_ROOT,
                    runner=self._remote_runner(repo, calls),
                )

            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["RepoFullName"], "Tax-Technology/goldilocks")
            self.assertEqual(state["Username"], "Tax-Technology")
            self.assertEqual(state["Repo"], "goldilocks")
            self.assertEqual(calls, [(["git", "remote", "get-url", "--all", "origin"], repo.resolve())])
            self.assertIn("--repo", gh_json.call_args_list[0].args[1])
            self.assertIn("Tax-Technology/goldilocks", gh_json.call_args_list[0].args[1])
            gh.assert_called_once()

    def test_switching_from_old_durable_issue_to_new_issue_needs_no_identity_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / CURRENT_DIR
            current.mkdir(parents=True)
            (current / "state.json").write_text(
                json.dumps({"IssueNumber": 5, "Status": "Prepared"}) + "\n",
                encoding="utf-8",
            )
            sentinel = current / "old-run-sentinel.txt"
            sentinel.write_text("old run\n", encoding="utf-8")
            issue, base_ref, base_commit = self._github_evidence()

            with (
                patch.dict(os.environ, {"LOCAL_CHECK": "autodev verify-local"}, clear=True),
                patch("automation.workflow_preparation.gh_json", side_effect=[issue, base_ref, base_commit]),
                patch("automation.workflow_preparation.validate_prepared_worktree", return_value="base-sha"),
                patch("automation.workflow_preparation.write_workspace_snapshot", side_effect=self._snapshot),
                patch("automation.workflow_preparation.gh"),
            ):
                prepared = workflow_preparation.ensure_prepared_issue(
                    repo,
                    "6",
                    autodev_root=REPO_ROOT,
                    runner=self._remote_runner(repo, []),
                )

            state = json.loads((prepared / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["IssueNumber"], 6)
            self.assertEqual(state["RepoFullName"], "Tax-Technology/goldilocks")
            self.assertFalse(sentinel.exists())

    def test_identity_failure_preserves_prior_durable_run_and_does_not_query_github(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / CURRENT_DIR
            current.mkdir(parents=True)
            previous_state = {"IssueNumber": 5, "Status": "ReadyForReview", "PrUrl": "https://example.test/pr/27"}
            state_path = current / "state.json"
            state_text = json.dumps(previous_state, sort_keys=True) + "\n"
            state_path.write_text(state_text, encoding="utf-8")
            sentinel = current / "durable-evidence.txt"
            sentinel.write_text("preserve me\n", encoding="utf-8")

            def non_github_runner(argv, **_kwargs):
                self.assertEqual(list(argv), ["git", "remote", "get-url", "--all", "origin"])
                return SimpleNamespace(
                    returncode=0,
                    stdout="https://gitlab.com/not/github.git\n",
                    stderr="",
                )

            with (
                patch.dict(os.environ, {}, clear=True),
                patch("automation.workflow_preparation.gh_json", side_effect=AssertionError("GitHub must not be queried")),
                patch("automation.workflow_preparation.gh", side_effect=AssertionError("GitHub must not be mutated")),
            ):
                with self.assertRaises(WorkflowStageError) as raised:
                    workflow_preparation.ensure_prepared_issue(
                        repo,
                        "6",
                        autodev_root=REPO_ROOT,
                        runner=non_github_runner,
                    )

            self.assertIn("Git remote 'origin'", str(raised.exception))
            self.assertEqual(state_path.read_text(encoding="utf-8"), state_text)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve me\n")


if __name__ == "__main__":
    unittest.main()

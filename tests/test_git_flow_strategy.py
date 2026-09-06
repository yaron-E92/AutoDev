from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import scheduler_process, workflow_github, workflow_preparation


BASE_SHA = "a" * 40
TREE_SHA = "b" * 40
HEAD_SHA = "c" * 40


class GitFlowWorkflowTests(unittest.TestCase):
    def _write_policy(self, repo: Path) -> None:
        (repo / ".autodev").mkdir(parents=True, exist_ok=True)
        (repo / ".autodev" / "repo.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "development": {
                        "strategy": "git-flow",
                        "integration_branch": "develop",
                        "release_branch": "main",
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_issue_preparation_uses_and_persists_git_flow_integration_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_policy(repo)

            def fake_gh_json(_repo, args, **_kwargs):
                if args[:2] == ["issue", "view"]:
                    return {
                        "number": 266,
                        "title": "Git Flow",
                        "body": "+semver: minor",
                        "url": "https://example.invalid/266",
                        "labels": [],
                    }
                endpoint = args[1] if args and args[0] == "api" and len(args) > 1 else ""
                if endpoint.endswith("/git/ref/heads/develop"):
                    return {"object": {"sha": BASE_SHA}}
                if endpoint.endswith(f"/git/commits/{BASE_SHA}"):
                    return {"tree": {"sha": TREE_SHA}}
                raise AssertionError(args)

            def fake_snapshot(_repo, path):
                path.write_text("{}\n", encoding="utf-8")

            with patch.object(workflow_preparation.repository_identity, "resolve_github_repository", return_value="owner/repo"), patch.object(
                workflow_preparation, "gh_json", side_effect=fake_gh_json
            ), patch.object(
                workflow_preparation, "gh", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")
            ), patch.object(
                workflow_preparation, "validate_prepared_worktree", return_value=BASE_SHA
            ), patch.object(
                workflow_preparation, "write_workspace_snapshot", side_effect=fake_snapshot
            ), patch.object(
                workflow_preparation, "resolve_profiles", return_value=("", "python -m unittest", "")
            ), patch.object(
                workflow_preparation.ux_workflow, "resolve_configured", return_value=None
            ), patch.object(
                workflow_preparation.ux_workflow, "evidence", return_value={}
            ), patch.dict(os.environ, {"BASE_BRANCH": ""}, clear=False):
                current = workflow_preparation.ensure_prepared_issue(repo, "266")

            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["DevelopmentStrategy"], "git-flow")
            self.assertEqual(state["IntegrationBranch"], "develop")
            self.assertEqual(state["ReleaseBranch"], "main")
            self.assertEqual(state["Base"], "develop")
            self.assertEqual(state["BaseSha"], BASE_SHA)

    def test_pr_creation_targets_persisted_integration_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (current / "issue.md").write_text("issue\n", encoding="utf-8")
            (current / "plan.md").write_text("plan\n", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_gh(_repo, args, **_kwargs):
                calls.append(list(args))
                return SimpleNamespace(
                    returncode=0,
                    stdout="https://github.com/owner/repo/pull/1\n",
                    stderr="",
                )

            state = {
                "RepoFullName": "owner/repo",
                "Base": "develop",
                "BranchName": "autodev/issue-266",
                "IssueTitle": "Git Flow",
                "LocalCheck": "python -m unittest",
                "SemVerIntent": "minor",
                "PrUrl": "",
                "PrNumber": 0,
            }
            with patch.object(workflow_github, "gh", side_effect=fake_gh), patch.object(
                workflow_github,
                "gh_json",
                return_value={"number": 1, "headRefOid": HEAD_SHA},
            ):
                workflow_github.ensure_pr(repo, current, state)

            create = next(args for args in calls if args[:2] == ["pr", "create"])
            self.assertEqual(create[create.index("--base") + 1], "develop")

    def test_scheduler_default_branch_becomes_git_flow_integration_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_policy(repo)

            def fake_git(_repo, args, **kwargs):
                if args[:3] == ["symbolic-ref", "--quiet", "--short"]:
                    return SimpleNamespace(returncode=0, stdout="origin/main\n", stderr="")
                raise AssertionError((args, kwargs))

            with patch.object(scheduler_process, "_git", side_effect=fake_git):
                self.assertEqual(scheduler_process._default_branch(repo), "develop")

    def test_scheduler_trunk_repository_retains_github_default_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)

            def fake_git(_repo, args, **kwargs):
                if args[:3] == ["symbolic-ref", "--quiet", "--short"]:
                    return SimpleNamespace(returncode=0, stdout="origin/stable\n", stderr="")
                raise AssertionError((args, kwargs))

            with patch.object(scheduler_process, "_git", side_effect=fake_git):
                self.assertEqual(scheduler_process._default_branch(repo), "stable")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    autodev_cli,
    repo_setup,
    semver_intent,
    workflow_github,
    workflow_preparation,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class AutoDevSemVerWorkflowTests(unittest.TestCase):
    def test_issue_to_pr_semver_override_is_normalized_and_forwarded(self) -> None:
        forwarded, error = autodev_cli._issue_to_pr(["71", "--semver", "NONE"])
        self.assertEqual(error, "")
        self.assertEqual(
            forwarded,
            ["coordinate", "--arguments", "71", "--semver", "none"],
        )

        invalid, error = autodev_cli._issue_to_pr(["71", "--semver", "feature"])
        self.assertIsNone(invalid)
        self.assertIn("expected one of", error)

    def test_repo_config_gets_patch_default_and_rejects_invalid_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            created: list[str] = []
            updated: list[str] = []
            repo_setup._ensure_repo_config(
                repo,
                enable_opencode=False,
                created=created,
                updated=updated,
            )
            config = json.loads(
                (repo / ".autodev" / "repo.json").read_text(encoding="utf-8")
            )
            self.assertEqual(config["default_semver_intent"], "patch")

            config["default_semver_intent"] = "feature"
            (repo / ".autodev" / "repo.json").write_text(
                json.dumps(config),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(repo_setup.RepoSetupError, "repository-default"):
                repo_setup._load_repo_config(repo)

    def test_prepare_persists_repository_default_before_roles_run(self) -> None:
        issue = {
            "number": 71,
            "title": "Seed demo data",
            "body": "Implement representative demo states.",
            "url": "https://github.test/owner/repo/issues/71",
            "labels": [],
        }
        base_ref = {"object": {"sha": "base-sha"}}
        base_commit = {"tree": {"sha": "tree-sha"}}

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            (repo / ".autodev").mkdir()
            (repo / ".autodev" / "repo.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "opencode": {"enabled": True},
                        "default_semver_intent": "none",
                    }
                ),
                encoding="utf-8",
            )

            def snapshot(_repo: Path, path: Path) -> None:
                path.write_text("{}\n", encoding="utf-8")

            with (
                patch(
                    "automation.workflow_preparation.repository_identity.resolve_github_repository",
                    return_value="owner/repo",
                ),
                patch(
                    "automation.workflow_preparation.gh_json",
                    side_effect=[issue, base_ref, base_commit],
                ),
                patch(
                    "automation.workflow_preparation.validate_prepared_worktree",
                    return_value="base-sha",
                ),
                patch(
                    "automation.workflow_preparation.resolve_profiles",
                    return_value=("", "python -m unittest", "Python"),
                ),
                patch(
                    "automation.workflow_preparation.write_workspace_snapshot",
                    side_effect=snapshot,
                ),
                patch("automation.workflow_preparation.gh"),
            ):
                current = workflow_preparation.ensure_prepared_issue(
                    repo,
                    "71",
                    autodev_root=REPO_ROOT,
                )

            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["SemVerIntent"], "none")
            self.assertEqual(state["SemVerIntentSource"], "repository-default")

    def test_prepare_rejects_duplicate_issue_directives_before_running_label_mutation(self) -> None:
        issue = {
            "number": 71,
            "title": "Seed demo data",
            "body": "+semver: patch\n+semver: minor\n",
            "url": "https://github.test/owner/repo/issues/71",
            "labels": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            with (
                patch(
                    "automation.workflow_preparation.repository_identity.resolve_github_repository",
                    return_value="owner/repo",
                ),
                patch("automation.workflow_preparation.gh_json", return_value=issue),
                patch("automation.workflow_preparation.gh") as gh,
            ):
                with self.assertRaisesRegex(
                    semver_intent.SemVerIntentError,
                    "duplicate/conflicting",
                ):
                    workflow_preparation.ensure_prepared_issue(repo, "71")
            gh.assert_not_called()

    def test_pr_body_contains_exactly_one_persisted_issue_intent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (current / "issue.md").write_text(
                "# Issue\n\nDetails\n\n+semver: minor\n",
                encoding="utf-8",
            )
            (current / "plan.md").write_text("Plan body\n", encoding="utf-8")
            state = {
                "RepoFullName": "owner/repo",
                "Base": "main",
                "BranchName": "autodev/issue-71",
                "IssueTitle": "Seed demo data",
                "IssueText": "# Issue\n\nDetails\n\n+semver: minor\n",
                "LocalCheck": "python -m unittest",
                "SemVerIntent": "minor",
                "SemVerIntentSource": "issue",
                "PrUrl": "",
            }
            (current / "state.json").write_text(
                json.dumps(state),
                encoding="utf-8",
            )
            with (
                patch(
                    "automation.workflow_github.gh",
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout="https://github.test/owner/repo/pull/9\n",
                        stderr="",
                    ),
                ),
                patch(
                    "automation.workflow_github.gh_json",
                    return_value={"number": 9, "headRefOid": "head-sha"},
                ),
            ):
                workflow_github.ensure_pr(repo, current, state)

            body = (current / "pr-body.md").read_text(encoding="utf-8")
            self.assertEqual(semver_intent.explicit_intents(body), ["minor"])
            self.assertTrue(body.rstrip().endswith("+semver: minor"))
            self.assertIn("Details", body)

    def test_legacy_prepared_state_without_intent_gets_patch_when_pr_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            (current / "issue.md").write_text("# Issue\n\nNo directive\n", encoding="utf-8")
            (current / "plan.md").write_text("Plan\n", encoding="utf-8")
            state = {
                "RepoFullName": "owner/repo",
                "Base": "main",
                "BranchName": "autodev/legacy",
                "IssueTitle": "Legacy run",
                "IssueText": "# Issue\n\nNo directive\n",
                "LocalCheck": "test",
                "PrUrl": "",
            }
            (current / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with (
                patch(
                    "automation.workflow_github.gh",
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout="https://github.test/owner/repo/pull/10\n",
                        stderr="",
                    ),
                ),
                patch(
                    "automation.workflow_github.gh_json",
                    return_value={"number": 10, "headRefOid": "head-sha"},
                ),
            ):
                workflow_github.ensure_pr(repo, current, state)

            self.assertEqual(state["SemVerIntent"], "patch")
            self.assertEqual(state["SemVerIntentSource"], "built-in-default")
            body = (current / "pr-body.md").read_text(encoding="utf-8")
            self.assertEqual(semver_intent.explicit_intents(body), ["patch"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import repo_setup


class RepoSetupDevelopmentTests(unittest.TestCase):
    def test_install_can_write_git_flow_policy_without_changing_schema_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            created: list[str] = []
            updated: list[str] = []

            repo_setup._ensure_repo_config(
                repo,
                enable_opencode=False,
                development_strategy="git-flow",
                integration_branch="develop",
                release_branch="main",
                created=created,
                updated=updated,
            )

            value = json.loads((repo / ".autodev" / "repo.json").read_text(encoding="utf-8"))
            self.assertEqual(value["version"], 1)
            self.assertEqual(
                value["development"],
                {
                    "strategy": "git-flow",
                    "integration_branch": "develop",
                    "release_branch": "main",
                },
            )
            self.assertEqual(created, [".autodev/repo.json"])
            self.assertEqual(updated, [])

    def test_install_without_strategy_preserves_existing_development_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            config = repo / ".autodev" / "repo.json"
            config.parent.mkdir()
            config.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "default_semver_intent": "patch",
                        "opencode": {"enabled": True},
                        "development": {
                            "strategy": "git-flow",
                            "integration_branch": "develop",
                            "release_branch": "main",
                        },
                    }
                ),
                encoding="utf-8",
            )
            created: list[str] = []
            updated: list[str] = []

            repo_setup._ensure_repo_config(
                repo,
                enable_opencode=True,
                created=created,
                updated=updated,
            )

            value = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(value["development"]["integration_branch"], "develop")
            self.assertEqual(updated, [])

    def test_git_flow_defaults_to_develop_and_main_when_selected_from_cli(self):
        self.assertEqual(
            repo_setup._requested_development("git-flow", "", ""),
            {
                "strategy": "git-flow",
                "integration_branch": "develop",
                "release_branch": "main",
            },
        )

    def test_branch_options_without_strategy_fail(self):
        with self.assertRaises(repo_setup.RepoSetupError):
            repo_setup._requested_development("", "develop", "main")

    def test_doctor_reports_strategy_and_missing_integration_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".git").mkdir()
            config = repo / ".autodev" / "repo.json"
            config.parent.mkdir()
            config.write_text(
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

            strategy = repo_setup._check_development_policy(repo)
            self.assertEqual(strategy.state, "ok")
            self.assertIn("strategy=git-flow", strategy.detail)
            self.assertIn("normal-pr-target=develop", strategy.detail)

            def fake_run_gh(_repo, args, **_kwargs):
                endpoint = args[1]
                if endpoint.endswith("/heads/develop"):
                    return SimpleNamespace(returncode=1, stdout="", stderr="not found")
                if endpoint.endswith("/heads/main"):
                    return SimpleNamespace(returncode=0, stdout="{}", stderr="")
                raise AssertionError(args)

            with patch.object(repo_setup.queue_github, "_run_gh", side_effect=fake_run_gh):
                checks = repo_setup._development_branch_checks(
                    repo,
                    "owner/repo",
                    runner=lambda *_args, **_kwargs: None,
                )

            by_name = {item.name: item for item in checks}
            self.assertEqual(by_name["development-integration-branch"].state, "error")
            self.assertIn("create it", by_name["development-integration-branch"].detail)
            self.assertEqual(by_name["development-release-branch"].state, "ok")


if __name__ == "__main__":
    unittest.main()

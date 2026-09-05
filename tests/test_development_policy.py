from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import development_policy


class DevelopmentPolicyTests(unittest.TestCase):
    def test_missing_policy_preserves_trunk_behavior(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            policy = development_policy.load_development_policy(
                Path(temp_dir),
                default_branch="main",
            )
        self.assertEqual(policy.strategy, "trunk")
        self.assertEqual(policy.integration_branch, "main")
        self.assertEqual(policy.release_branch, "main")

    def test_explicit_trunk_uses_one_branch(self):
        policy = development_policy.parse_development_policy(
            {
                "strategy": "trunk",
                "integration_branch": "stable",
                "release_branch": "stable",
            },
            default_branch="main",
        )
        self.assertEqual(policy.normal_work_branch, "stable")

    def test_git_flow_requires_distinct_integration_and_release_branches(self):
        policy = development_policy.parse_development_policy(
            {
                "strategy": "git-flow",
                "integration_branch": "develop",
                "release_branch": "main",
            }
        )
        self.assertEqual(policy.strategy, "git-flow")
        self.assertEqual(policy.normal_work_branch, "develop")
        self.assertEqual(policy.release_branch, "main")

        with self.assertRaises(development_policy.DevelopmentPolicyError):
            development_policy.parse_development_policy(
                {
                    "strategy": "git-flow",
                    "integration_branch": "main",
                    "release_branch": "main",
                }
            )

    def test_git_flow_requires_both_branch_names(self):
        with self.assertRaises(development_policy.DevelopmentPolicyError):
            development_policy.parse_development_policy(
                {"strategy": "git-flow", "integration_branch": "develop"}
            )

    def test_unknown_strategy_fails(self):
        with self.assertRaises(development_policy.DevelopmentPolicyError):
            development_policy.parse_development_policy({"strategy": "mystery"})

    def test_unsafe_branch_names_are_rejected(self):
        for value in (
            "feature branch",
            "../main",
            "main..other",
            "refs/@{bad}",
            "-danger",
            "topic.lock",
            ".hidden/topic",
            "topic\\evil",
        ):
            with self.subTest(value=value):
                with self.assertRaises(development_policy.DevelopmentPolicyError):
                    development_policy.validate_branch_name(value)

    def test_explicit_base_override_remains_supported_and_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            (repo / ".autodev").mkdir()
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
            self.assertEqual(
                development_policy.normal_work_branch(repo),
                "develop",
            )
            self.assertEqual(
                development_policy.normal_work_branch(repo, explicit="release/test"),
                "release/test",
            )


if __name__ == "__main__":
    unittest.main()

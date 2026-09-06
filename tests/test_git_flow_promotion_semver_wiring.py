from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitFlowPromotionSemVerWiringTests(unittest.TestCase):
    def test_reusable_version_intent_resolves_authoritative_pr_branch_roles(self):
        text = (ROOT / ".github" / "workflows" / "version-intent.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("Resolve current pull request metadata", text)
        self.assertIn(".base.ref", text)
        self.assertIn(".head.ref", text)
        self.assertIn("pr-base: ${{ steps.pr.outputs.base_ref }}", text)
        self.assertIn("pr-head: ${{ steps.pr.outputs.head_ref }}", text)
        self.assertIn("github-token: ${{ github.token }}", text)

    def test_version_policy_action_exposes_branch_role_inputs(self):
        text = (ROOT / ".github" / "actions" / "version-policy" / "action.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("  pr-base:", text)
        self.assertIn("  pr-head:", text)

    def test_promotion_semver_documentation_is_not_generic_exact_one_rule(self):
        text = (ROOT / "docs" / "version-policy.md").read_text(encoding="utf-8")
        self.assertIn("develop -> main", text)
        self.assertIn("does not need to restate a bump", text)


if __name__ == "__main__":
    unittest.main()

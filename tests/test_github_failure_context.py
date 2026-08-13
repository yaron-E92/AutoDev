from __future__ import annotations

import unittest

from automation import github_cli_proxy


class GitHubFailureContextTests(unittest.TestCase):
    def test_api_label_includes_method_and_endpoint(self):
        value = github_cli_proxy.operation_label(
            ["api", "repos/o/r/git/trees", "--method", "POST"]
        )
        self.assertEqual(value, "GitHub API POST repos/o/r/git/trees")

    def test_pr_label_is_bounded(self):
        value = github_cli_proxy.operation_label(
            ["pr", "view", "52", "--json", "headRefOid"]
        )
        self.assertEqual(value, "GitHub PR view")

    def test_plain_not_found_does_not_guess_workflow_permission(self):
        value = github_cli_proxy.workflow_authorization_hint(
            "gh: Not Found (HTTP 404)"
        )
        self.assertEqual(value, "")

    def test_workflow_permission_evidence_gets_actionable_hint(self):
        value = github_cli_proxy.workflow_authorization_hint(
            "workflow update forbidden: permission denied"
        )
        self.assertIn("workflow-file", value)


if __name__ == "__main__":
    unittest.main()

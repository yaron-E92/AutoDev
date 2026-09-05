from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GitFlowWorkflowWiringTests(unittest.TestCase):
    def test_develop_ci_calls_shared_ci_without_release_tagging(self):
        text = (ROOT / ".github" / "workflows" / "ci-develop.yml").read_text(encoding="utf-8")
        self.assertIn("- develop", text)
        self.assertIn("uses: ./.github/workflows/ci.yml", text)
        self.assertNotIn("version-tag.yml", text)
        self.assertNotIn("- edited", text)

    def test_metadata_version_intent_runs_for_main_and_develop(self):
        text = (ROOT / ".github" / "workflows" / "pr-version-intent.yml").read_text(encoding="utf-8")
        self.assertIn("- main", text)
        self.assertIn("- develop", text)
        self.assertIn("- edited", text)

    def test_public_tag_job_remains_release_branch_only(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("uses: ./.github/workflows/version-tag.yml", text)
        self.assertIn("branch: main", text)


if __name__ == "__main__":
    unittest.main()

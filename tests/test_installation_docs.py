from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class InstallationDocumentationTests(unittest.TestCase):
    def test_primary_installation_and_queue_docs_prefer_first_class_cli(self):
        installation = (REPO_ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
        queue = (REPO_ROOT / "docs" / "queue.md").read_text(encoding="utf-8")

        self.assertIn("autodev --help", installation)
        self.assertIn("autodev issue-to-pr 123", installation)
        self.assertIn("autodev repo install", installation)
        self.assertIn("autodev doctor", installation)
        self.assertIn("autodev queue next", installation)
        self.assertIn("autodev queue reconcile", queue)

    def test_installation_docs_define_ownership_and_opencode_config_authority(self):
        installation = (REPO_ROOT / "docs" / "installation.md").read_text(encoding="utf-8")

        self.assertIn(".autodev/", installation)
        self.assertIn(".autodev-run/", installation)
        self.assertIn(".opencode/", installation)
        self.assertIn("opencode.jsonc", installation)
        self.assertIn("remains authoritative", installation)
        self.assertNotIn("temporary compatibility shim", installation)
        self.assertNotIn(".opencode/autodev.json", installation)

    def test_installation_docs_separate_runtime_and_contributor_workflows(self):
        installation = (REPO_ROOT / "docs" / "installation.md").read_text(encoding="utf-8")

        self.assertIn("## Runtime and provider configuration", installation)
        self.assertIn("AUTODEV_ROLE_RUNTIME", installation)
        self.assertIn("autodev models", installation)
        self.assertIn("## Contributor development", installation)
        self.assertIn("source-development checks", installation)
        self.assertIn("python -m unittest discover -s tests -v", installation)


if __name__ == "__main__":
    unittest.main()

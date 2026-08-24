from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def _read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_readme_uses_installed_cli_and_canonical_issue_workflow(self) -> None:
        readme = self._read("README.md")
        self.assertIn("autodev issue-to-pr 123", readme)
        self.assertIn("autodev repo install", readme)
        self.assertIn("autodev doctor", readme)
        self.assertNotIn("From an AutoDev checkout", readme)
        self.assertNotIn("autodev-vX.Y.Z-linux.zip", readme)

    def test_installation_separates_user_repo_and_contributor_workflows(self) -> None:
        installation = self._read("docs/installation.md")
        for heading in (
            "## User installation",
            "## Configure a target repository",
            "## Contributor development",
        ):
            self.assertIn(heading, installation)
        self.assertIn("autodev issue-to-pr 123", installation)
        self.assertIn("does not need to clone", installation)

    def test_opencode_guide_uses_first_class_issue_to_pr_spelling(self) -> None:
        opencode = self._read("docs/opencode.md")
        self.assertIn("autodev issue-to-pr 123", opencode)
        self.assertIn("advanced/integration spelling", opencode)
        self.assertNotIn(
            "Equivalent first-class CLI:\n\n```text\nautodev coordinate --arguments 123",
            opencode,
        )

    def test_release_guide_matches_current_packager_outputs(self) -> None:
        releases = self._read("docs/releases.md")
        self.assertIn("autodev-vX.Y.Z-common.zip", releases)
        self.assertIn("autodev-vX.Y.Z-windows.zip", releases)
        self.assertIn("workflow_dispatch", releases)
        self.assertNotIn("autodev-vX.Y.Z-linux.zip", releases)
        self.assertIn("Native Windows MSI and Linux DEB/RPM packages are **not available yet**", releases)

    def test_workspace_guide_preserves_repository_owned_autodev_policy(self) -> None:
        workspace = self._read("docs/workspace-scope.md")
        self.assertIn("The `.autodev/` directory is the target repository's AutoDev policy/configuration boundary", workspace)
        self.assertIn("`.autodev-run/` is durable execution state", workspace)
        self.assertNotIn(".autodev/\n.serena/", workspace)

    def test_release_common_bundle_declares_shipped_docs(self) -> None:
        packaging = self._read("automation/package_release.py")
        self.assertIn('    "docs",', packaging)


if __name__ == "__main__":
    unittest.main()

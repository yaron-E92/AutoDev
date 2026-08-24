from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (
    "README.md",
    "docs/headroom.md",
    "docs/installation.md",
    "docs/model-roles.md",
    "docs/opencode.md",
    "docs/privacy.md",
    "docs/releases.md",
    "docs/scheduler.md",
    "docs/windows-verification.md",
    "docs/workspace-scope.md",
    "examples/opencode/README.md",
)
LOCAL_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
OPENCODE_AGENTS = (
    "autodev-coordinator",
    "autodev-reader",
    "autodev-synthesizer",
    "autodev-planner",
    "autodev-implementer",
    "autodev-fixer",
    "autodev-verifier",
)


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

    def test_model_docs_list_every_opencode_agent_mapping(self) -> None:
        roles = self._read("docs/model-roles.md")
        opencode = self._read("docs/opencode.md")
        for agent in OPENCODE_AGENTS:
            self.assertIn(agent, roles)
            self.assertIn(agent, opencode)
        self.assertIn("six model-backed **workflow roles**", roles)
        self.assertIn("Python remains authoritative", roles)
        self.assertIn("autodev models", roles)
        self.assertNotIn("`automation.opencode_adapter_models`", roles)

    def test_headroom_guide_respects_opencode_transport_boundary(self) -> None:
        headroom = self._read("docs/headroom.md")
        self.assertIn("autodev issue-to-pr 123", headroom)
        self.assertIn("provider-layer Headroom proxy is **not** automatically in that transport path", headroom)
        self.assertNotIn("headroom wrap opencode\n```", headroom)
        self.assertNotIn("autodev coordinate --arguments 123", headroom)

    def test_privacy_guide_uses_time_bounded_revocable_grants(self) -> None:
        privacy = self._read("docs/privacy.md")
        for command in (
            "autodev privacy consent",
            "autodev privacy status",
            "autodev privacy revoke <grant-id>",
            "autodev privacy revoke --all",
        ):
            self.assertIn(command, privacy)
        for duration in ("24h", "7d", "30d", "until-revoked"):
            self.assertIn(duration, privacy)
        self.assertIn("headless or scheduled run can consume a matching active grant", privacy)
        self.assertNotIn("AUTODEV_PRIVACY_CONSENT", privacy)

    def test_release_guide_matches_current_packager_outputs(self) -> None:
        releases = self._read("docs/releases.md")
        self.assertIn("autodev-vX.Y.Z-common.zip", releases)
        self.assertIn("autodev-vX.Y.Z-windows.zip", releases)
        self.assertIn("workflow_dispatch", releases)
        self.assertNotIn("autodev-vX.Y.Z-linux.zip", releases)
        self.assertIn("Native Windows MSI and Linux DEB/RPM packages are **not available yet**", releases)

    def test_scheduler_prerequisites_assume_installed_cli(self) -> None:
        scheduler = self._read("docs/scheduler.md")
        prerequisites = scheduler.split("## Install", 1)[0]
        self.assertIn("docs/installation.md", self._read("README.md"))
        self.assertIn("autodev repo install", prerequisites)
        self.assertIn("autodev doctor", prerequisites)
        self.assertNotIn("autodev install --user", prerequisites)

    def test_windows_verification_uses_public_repository_setup(self) -> None:
        windows = self._read("docs/windows-verification.md")
        self.assertIn("autodev repo install", windows)
        self.assertNotIn("python -m automation.opencode_install", windows)
        self.assertNotIn("python3 -m automation.opencode_install", windows)

    def test_workspace_guide_preserves_repository_owned_autodev_policy(self) -> None:
        workspace = self._read("docs/workspace-scope.md")
        self.assertIn("The `.autodev/` directory is the target repository's AutoDev policy/configuration boundary", workspace)
        self.assertIn("`.autodev-run/` is durable execution state", workspace)
        self.assertNotIn(".autodev/\n.serena/", workspace)

    def test_release_common_bundle_declares_shipped_docs(self) -> None:
        packaging = self._read("automation/package_release.py")
        self.assertIn('    "docs",', packaging)

    def test_changed_public_docs_have_no_broken_local_links(self) -> None:
        broken: list[str] = []
        for relative in PUBLIC_DOCS:
            source = ROOT / relative
            for raw_target in LOCAL_LINK.findall(source.read_text(encoding="utf-8")):
                target = raw_target.split("#", 1)[0].strip()
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (source.parent / target).resolve()
                if not resolved.exists():
                    broken.append(f"{relative} -> {raw_target}")
        self.assertEqual(broken, [])


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path

from automation import opencode_adapter_contract, opencode_install

REPO_ROOT = Path(__file__).resolve().parents[1]


class OpenCodeLocalCoordinatorHardeningTests(unittest.TestCase):
    def test_canonical_installed_coordinator_is_closed_world_and_uses_autodev_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            opencode_install.install_assets(target, REPO_ROOT)
            coordinator = (target / ".opencode" / "agents" / "autodev-coordinator.md").read_text(encoding="utf-8")

        self.assertIn('permission:\n  "*": deny', coordinator)
        self.assertNotIn(".opencode/autodev", coordinator)
        self.assertIn('"autodev stage *": allow', coordinator)
        self.assertIn("Canonical AutoDev launcher", coordinator)
        self.assertIn("installed `autodev` command", coordinator)
        self.assertIn("do not probe", coordinator.casefold())
        self.assertIn("Unrelated built-in, plugin, or MCP tools are denied by default", coordinator)

    def test_all_canonical_installed_role_agents_use_first_class_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            opencode_install.install_assets(target, REPO_ROOT)
            for name in opencode_adapter_contract.AGENT_FILES:
                text = (target / ".opencode" / "agents" / name).read_text(encoding="utf-8")
                with self.subTest(agent=name):
                    self.assertNotIn(".opencode/autodev", text)
                    self.assertIn("Canonical AutoDev launcher", text)
                    self.assertIn("installed `autodev` command", text)
                    self.assertIn("do not probe", text.casefold())


if __name__ == "__main__":
    unittest.main()

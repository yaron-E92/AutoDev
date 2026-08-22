import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import opencode_adapter, opencode_install


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


def load_bridge_module():
    path = OPEN_CODE_ROOT / "autodev.py"
    spec = importlib.util.spec_from_file_location("autodev_portable_bridge", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load bridge module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenCodeLocalCoordinatorHardeningTests(unittest.TestCase):
    def setUp(self):
        self.bridge = load_bridge_module()

    def test_github_identity_parses_common_github_remotes(self):
        cases = {
            "git@github.com:yaron-E92/AutoDev.git": ("yaron-E92", "AutoDev"),
            "https://github.com/yaron-E92/AutoDev.git": ("yaron-E92", "AutoDev"),
            "ssh://git@github.com/yaron-E92/AutoDev.git": ("yaron-E92", "AutoDev"),
            "http://github.com/yaron-E92/AutoDev": ("yaron-E92", "AutoDev"),
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(self.bridge._github_identity_from_remote(remote), expected)

        self.assertIsNone(
            self.bridge._github_identity_from_remote(
                "https://gitlab.com/yaron-E92/AutoDev.git"
            )
        )
        self.assertIsNone(self.bridge._github_identity_from_remote("not-a-remote"))

    def test_missing_github_identity_is_derived_without_overriding_explicit_values(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout="git@github.com:yaron-E92/AutoDev.git\n",
                stderr="",
            )

        env = {"REMOTE_NAME": "upstream", "GITHUB_OWNER": "explicit-owner"}
        self.bridge._resolve_github_environment(env, Path("repo"), runner=runner)

        self.assertEqual(env["GITHUB_OWNER"], "explicit-owner")
        self.assertEqual(env["GITHUB_REPO"], "AutoDev")
        self.assertEqual(calls[0][0], ["git", "remote", "get-url", "upstream"])

    def test_complete_explicit_github_identity_skips_remote_lookup(self):
        def runner(*args, **kwargs):
            raise AssertionError("remote lookup should not run")

        env = {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo"}
        self.bridge._resolve_github_environment(env, Path("repo"), runner=runner)
        self.assertEqual(env, {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo"})

    def test_bridge_environment_injects_derived_identity_and_configured_launcher(self):
        def runner(command, **kwargs):
            return SimpleNamespace(
                returncode=0,
                stdout="https://github.com/yaron-E92/TATATORPLAG.git\n",
                stderr="",
            )

        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            self.bridge.os.environ,
            {},
            clear=True,
        ):
            env = self.bridge._bridge_environment(
                "python3",
                Path(temp_dir),
                Path(temp_dir),
                runner=runner,
            )

        self.assertEqual(env["GITHUB_OWNER"], "yaron-E92")
        self.assertEqual(env["GITHUB_REPO"], "TATATORPLAG")
        self.assertEqual(env["AUTODEV_PYTHON"], "python3")

    def test_bridge_config_loader_remains_only_for_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "autodev.json"

            valid = {
                "version": 1,
                "autodev_root": str(root),
                "python": "python",
            }
            config_path.write_text(json.dumps(valid), encoding="utf-8")
            resolved_root, python = self.bridge._load_config(config_path)
            self.assertEqual(resolved_root, root)
            self.assertEqual(python, "python")

            for invalid, expected in (
                ({"autodev_root": str(root), "python": "python"}, "version"),
                ({"version": 1, "python": "python"}, "autodev_root"),
                ({"version": 1, "autodev_root": str(root)}, "python"),
            ):
                config_path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.subTest(invalid=invalid), self.assertRaises(ValueError) as raised:
                    self.bridge._load_config(config_path)
                self.assertIn(expected, str(raised.exception))

    def test_canonical_installed_coordinator_is_closed_world_and_uses_autodev_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            opencode_install.install_assets(target, REPO_ROOT, python_command="python3")
            coordinator = (target / ".opencode" / "agents" / "autodev-coordinator.md").read_text(
                encoding="utf-8"
            )

        self.assertIn('permission:\n  "*": deny', coordinator)
        self.assertNotIn(".opencode/autodev.json", coordinator)
        self.assertIn('"autodev stage *": allow', coordinator)
        self.assertIn("Canonical AutoDev launcher", coordinator)
        self.assertIn("installed `autodev` command", coordinator)
        self.assertIn("do not probe", coordinator.casefold())
        self.assertIn("Unrelated built-in, plugin, or MCP tools are denied by default", coordinator)

    def test_all_canonical_installed_role_agents_use_first_class_launcher_without_config_duplication(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            opencode_install.install_assets(target, REPO_ROOT, python_command="python3")
            for name in opencode_adapter.AGENT_FILES:
                path = target / ".opencode" / "agents" / name
                text = path.read_text(encoding="utf-8")
                with self.subTest(agent=name):
                    self.assertNotIn(".opencode/autodev.json", text)
                    self.assertIn("Canonical AutoDev launcher", text)
                    self.assertIn("installed `autodev` command", text)
                    self.assertIn("do not probe", text.casefold())


if __name__ == "__main__":
    unittest.main()

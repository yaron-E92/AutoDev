from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import user_install


REPO_ROOT = Path(__file__).resolve().parents[1]


class UserInstallTests(unittest.TestCase):
    def test_posix_user_install_is_idempotent_and_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            bin_dir = home / ".local" / "bin"

            first = user_install.install_user(
                REPO_ROOT,
                python="python3",
                bin_dir=bin_dir,
                platform_name="posix",
                home=home,
                path_value="",
            )
            first_text = (bin_dir / "autodev").read_text(encoding="utf-8")
            second = user_install.install_user(
                REPO_ROOT,
                python="python3",
                bin_dir=bin_dir,
                platform_name="posix",
                home=home,
                path_value="",
            )
            second_text = (bin_dir / "autodev").read_text(encoding="utf-8")

            self.assertEqual(first_text, second_text)
            self.assertEqual(first.bin_dir, second.bin_dir)
            self.assertIn("automation.autodev_cli", second_text)
            self.assertIn(str(REPO_ROOT), second_text)
            self.assertTrue((bin_dir / "autodev").stat().st_mode & 0o111)
            self.assertTrue(user_install.install_state_path(home=home).is_file())

    def test_windows_user_install_generates_cmd_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            bin_dir = home / "AutoDev" / "bin"

            result = user_install.install_user(
                REPO_ROOT,
                python=r"C:\Python\python.exe",
                bin_dir=bin_dir,
                platform_name="windows",
                home=home,
                path_value="",
            )
            launcher = bin_dir / "autodev.cmd"
            text = launcher.read_text(encoding="utf-8")

            self.assertEqual(result.platform, "windows")
            self.assertTrue(launcher.is_file())
            self.assertIn("automation.autodev_cli", text)
            self.assertIn(r"C:\Python\python.exe", text)
            self.assertNotIn(".opencode", text)

    def test_profile_edit_is_minimal_idempotent_and_reversible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            profile = home / ".profile"
            profile.write_text("export EXISTING=value\n", encoding="utf-8")
            bin_dir = home / ".local" / "bin"

            user_install.install_user(
                REPO_ROOT,
                python="python3",
                bin_dir=bin_dir,
                platform_name="posix",
                home=home,
                profiles=[profile],
                add_to_path=True,
                path_value="",
            )
            once = profile.read_text(encoding="utf-8")
            user_install.install_user(
                REPO_ROOT,
                python="python3",
                bin_dir=bin_dir,
                platform_name="posix",
                home=home,
                profiles=[profile],
                add_to_path=True,
                path_value="",
            )
            twice = profile.read_text(encoding="utf-8")

            self.assertEqual(once, twice)
            self.assertEqual(twice.count(user_install.PROFILE_BEGIN), 1)
            self.assertIn("export EXISTING=value", twice)
            self.assertIn(str(bin_dir), twice)

            user_install.uninstall_user(home=home)
            after = profile.read_text(encoding="utf-8")
            self.assertIn("export EXISTING=value", after)
            self.assertNotIn(user_install.PROFILE_BEGIN, after)
            self.assertFalse((bin_dir / "autodev").exists())

    def test_uninstall_does_not_touch_unrelated_user_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            unrelated = home / ".autodev" / "keep.txt"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("keep", encoding="utf-8")
            bin_dir = home / ".local" / "bin"

            user_install.install_user(
                REPO_ROOT,
                bin_dir=bin_dir,
                platform_name="posix",
                home=home,
            )
            user_install.uninstall_user(home=home)

            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import native_packaging


class NativePackagingTests(unittest.TestCase):
    def test_package_version_strips_only_semver_v_prefix(self) -> None:
        self.assertEqual(native_packaging.package_version("v1.10.2"), "1.10.2")

    def test_build_info_is_stable_and_tied_to_source_identity(self) -> None:
        text = native_packaging.build_info_text(
            "v2.3.4",
            "abcdef1234567890abcdef1234567890abcdef12",
        )
        self.assertEqual(
            json.loads(text),
            {
                "schema_version": 1,
                "version": "v2.3.4",
                "commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
            },
        )
        self.assertTrue(text.endswith("\n"))

    def test_pyinstaller_command_is_onedir_and_embeds_product_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo with spaces"
            out = Path(temp_dir) / "output with spaces"
            work = Path(temp_dir) / "work"
            (repo / "packaging").mkdir(parents=True)
            (repo / "packaging" / "autodev_entry.py").write_text("pass\n", encoding="utf-8")
            for relative in ("integrations", "promptTemplates", "agentFiles", "docs"):
                (repo / relative).mkdir(parents=True)
            for relative in ("README.md", "CONTRIBUTING.md", "codex-profiles.json"):
                (repo / relative).write_text("fixture\n", encoding="utf-8")
            build_info = work / "autodev-build.json"

            command = native_packaging.pyinstaller_command(
                repo,
                out,
                work,
                build_info,
                windows=False,
            )

        self.assertIn("--onedir", command)
        self.assertIn("--noupx", command)
        self.assertIn("--collect-submodules", command)
        self.assertIn("automation", command)
        self.assertIn("area_reader", command)
        data_values = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "--add-data"]
        self.assertTrue(any("integrations" in value for value in data_values))
        self.assertTrue(any("autodev-build.json" in value for value in data_values))
        # Commands are argument arrays rather than shell strings, so paths containing spaces remain atomic.
        self.assertIn(str(repo), command)
        self.assertIn(str(out), command)

    def test_windows_add_data_uses_windows_separator(self) -> None:
        value = native_packaging._data_argument(Path(r"C:\Auto Dev\docs"), "docs", windows=True)
        self.assertIn(";docs", value)


if __name__ == "__main__":
    unittest.main()

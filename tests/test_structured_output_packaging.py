from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import native_packaging


class StructuredOutputPackagingTests(unittest.TestCase):
    def test_pyinstaller_payload_embeds_role_output_schemas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            (repo / "packaging").mkdir(parents=True)
            (repo / "packaging" / "autodev_entry.py").write_text("pass\n", encoding="utf-8")
            schemas = repo / "automation" / "schemas"
            schemas.mkdir(parents=True)
            (schemas / "verifier-v1.json").write_text("{}\n", encoding="utf-8")

            command = native_packaging.pyinstaller_command(
                repo,
                root / "out",
                root / "work",
                root / "work" / "autodev-build.json",
                windows=False,
            )

        data_values = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "--add-data"
        ]
        self.assertTrue(
            any(
                "automation/schemas" in value
                and value.endswith(":automation/schemas")
                for value in data_values
            ),
            data_values,
        )


if __name__ == "__main__":
    unittest.main()

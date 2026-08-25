from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automation import autodev_cli, product_runtime


class ProductRuntimeTests(unittest.TestCase):
    def test_build_info_reports_packaged_version_and_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / product_runtime.BUILD_INFO_FILE).write_text(
                json.dumps(
                    {
                        "version": "v2.3.4",
                        "commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(product_runtime.version(root), "v2.3.4")
            self.assertEqual(
                product_runtime.commit_sha(root),
                "abcdef1234567890abcdef1234567890abcdef12",
            )
            self.assertEqual(
                product_runtime.version_text(root),
                "autodev v2.3.4 (abcdef123456)",
            )

    def test_cli_version_uses_packaged_build_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / product_runtime.BUILD_INFO_FILE).write_text(
                '{"version":"v9.8.7","commit_sha":"1234567890abcdef"}\n',
                encoding="utf-8",
            )
            output = io.StringIO()
            with patch.object(product_runtime, "product_root", return_value=root), redirect_stdout(output):
                code = autodev_cli.run(["--version"])

        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().strip(), "autodev v9.8.7 (1234567890ab)")

    def test_source_checkout_without_build_info_is_marked_development(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(product_runtime.version(Path(temp_dir)), "development")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import revision_cli


class CombinedRevisionTests(unittest.TestCase):
    def test_refresh_and_manual_instructions_are_composable(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            revision_cli.revision,
            "begin_revision",
            return_value={
                "revision_id": "r-combined",
                "previous_issue_sha256": "old",
                "refreshed_issue_sha256": "new",
                "issue_changed": True,
            },
        ) as begin, patch.object(
            revision_cli.opencode_entrypoint,
            "run",
            return_value=0,
        ):
            code = revision_cli.run_cli(
                [
                    "--repo",
                    temp_dir,
                    "--refresh-issue",
                    "--instructions",
                    "Preserve compatible work but follow the refreshed acceptance criteria.",
                ]
            )

        self.assertEqual(code, 0)
        begin.assert_called_once_with(
            Path(temp_dir).resolve(),
            instructions="Preserve compatible work but follow the refreshed acceptance criteria.",
            instructions_file=None,
            refresh_issue=True,
        )


if __name__ == "__main__":
    unittest.main()

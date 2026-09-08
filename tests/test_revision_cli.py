from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import revision, revision_cli


class RevisionCliTests(unittest.TestCase):
    def test_inline_revision_prepares_and_resumes_same_repository(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            revision_cli.revision,
            "begin_revision",
            return_value={
                "revision_id": "r-test",
                "previous_issue_sha256": "old",
                "refreshed_issue_sha256": "",
                "issue_changed": False,
            },
        ) as begin, patch.object(
            revision_cli.opencode_entrypoint,
            "run",
            return_value=0,
        ) as resume:
            code = revision_cli.run_cli(
                [
                    "--repo",
                    temp_dir,
                    "--runtime",
                    "opencode",
                    "--instructions",
                    "Preserve the useful work and change only the boundary.",
                ],
                stdout=stdout,
            )

        self.assertEqual(code, 0)
        begin.assert_called_once_with(
            Path(temp_dir).resolve(),
            instructions="Preserve the useful work and change only the boundary.",
            instructions_file=None,
            refresh_issue=False,
        )
        resume.assert_called_once_with(
            [
                "coordinate",
                "--resume",
                "--repo",
                str(Path(temp_dir).resolve()),
                "--runtime",
                "opencode",
            ]
        )
        self.assertIn("Revision prepared: r-test", stdout.getvalue())
        self.assertIn("preserve the current implementation", stdout.getvalue())

    def test_refresh_issue_reports_old_and_new_authority_hashes(self):
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            revision_cli.revision,
            "begin_revision",
            return_value={
                "revision_id": "r-refresh",
                "previous_issue_sha256": "old-hash",
                "refreshed_issue_sha256": "new-hash",
                "issue_changed": True,
            },
        ), patch.object(
            revision_cli.opencode_entrypoint,
            "run",
            return_value=0,
        ):
            code = revision_cli.run_cli(
                ["--repo", temp_dir, "--refresh-issue"],
                stdout=stdout,
            )

        self.assertEqual(code, 0)
        rendered = stdout.getvalue()
        self.assertIn("Issue source changed:", rendered)
        self.assertIn("previous: old-hash", rendered)
        self.assertIn("current:  new-hash", rendered)

    def test_instructions_file_is_forwarded_as_authority_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            instructions = Path(temp_dir) / "correction.md"
            instructions.write_text("Correct the integration boundary.\n", encoding="utf-8")
            with patch.object(
                revision_cli.revision,
                "begin_revision",
                return_value={"revision_id": "r-file"},
            ) as begin, patch.object(
                revision_cli.opencode_entrypoint,
                "run",
                return_value=0,
            ):
                code = revision_cli.run_cli(
                    [
                        "--repo",
                        temp_dir,
                        "--instructions-file",
                        str(instructions),
                    ]
                )

        self.assertEqual(code, 0)
        self.assertEqual(
            begin.call_args.kwargs["instructions_file"].resolve(),
            instructions.resolve(),
        )
        self.assertEqual(begin.call_args.kwargs["instructions"], "")

    def test_revision_validation_error_does_not_enter_coordinator(self):
        stderr = io.StringIO()
        with patch.object(
            revision_cli.revision,
            "begin_revision",
            side_effect=revision.RevisionError("no resumable run"),
        ), patch.object(revision_cli.opencode_entrypoint, "run") as resume:
            code = revision_cli.run_cli(
                ["--instructions", "change it"],
                stderr=stderr,
            )

        self.assertEqual(code, 2)
        resume.assert_not_called()
        self.assertIn("autodev revise: no resumable run", stderr.getvalue())

    def test_help_registration_makes_revise_public(self):
        revision_cli.register_help()
        self.assertIn(("revise",), revision_cli.cli_help.HELP)
        self.assertIn("revise", revision_cli.cli_help.KNOWN_TOP_LEVEL)
        entry = revision_cli.cli_help.HELP[("revise",)]
        self.assertIn("--refresh-issue", entry.usage)
        self.assertIn("operator-directed", entry.description)


if __name__ == "__main__":
    unittest.main()

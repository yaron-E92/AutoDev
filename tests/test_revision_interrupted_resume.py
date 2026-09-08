from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import (
    autodev_cli,
    opencode_resume_status,
    revision,
    revision_cli,
    run_manifest,
)
from tests.test_revision import RevisionTests


class InterruptedRevisionResumeTests(unittest.TestCase):
    def test_interrupted_revision_resumes_from_durable_synthesizer_checkpoint(self):
        fixture = RevisionTests()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, manifest_path = fixture._prepared_run(repo)

            # Simulate the process being interrupted after `autodev revise` has
            # durably prepared the revision but before its automatic coordinator
            # resume can make any role progress.
            with patch.object(
                revision,
                "_git_stdout",
                return_value="head-sha",
            ), fixture._source_patch(), patch.object(
                revision_cli.opencode_entrypoint,
                "run",
                side_effect=KeyboardInterrupt("simulated interruption"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    revision_cli.run_cli(
                        [
                            "--repo",
                            str(repo),
                            "--instructions",
                            "Preserve compatible work and revise the API boundary.",
                        ]
                    )

            # Everything needed to continue must come back from disk, not from
            # the interrupted `revise` invocation's in-memory state.
            active = revision.load_active(repo)
            manifest = run_manifest.load_manifest(manifest_path)
            state = json.loads((current / "state.json").read_text(encoding="utf-8"))

            self.assertEqual(active["status"], "active")
            self.assertEqual(active["current_revised_stage"], "synthesizer")
            self.assertEqual(
                opencode_resume_status.resume_action(manifest, state),
                "synthesizer",
            )
            self.assertIn(
                "Preserve compatible work and revise the API boundary.",
                revision.role_prompt_context(repo, "synthesizer"),
            )

            # The ordinary public resume command re-enters the canonical
            # coordinator resume path; it does not require a second `revise`.
            with patch.object(
                autodev_cli.opencode_entrypoint,
                "run",
                return_value=0,
            ) as resume:
                code = autodev_cli.run(["resume", "--repo", str(repo)])

            self.assertEqual(code, 0)
            resume.assert_called_once_with(
                ["coordinate", "--resume", "--repo", str(repo)]
            )


if __name__ == "__main__":
    unittest.main()

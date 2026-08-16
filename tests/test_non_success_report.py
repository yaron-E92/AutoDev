from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import non_success_report, workflow_stages


class NonSuccessReportTests(unittest.TestCase):
    def _repo(self, temp_dir: str) -> tuple[Path, Path]:
        repo = Path(temp_dir)
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        return repo, current

    def test_failed_report_is_actionable_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            workflow_stages.write_json(
                current / "state.json",
                {
                    "IssueNumber": 127,
                    "BranchName": "autodev/issue-127",
                    "LastLocalCheckPassed": False,
                    "LastCommitSha": "commit-sha",
                    "PrUrl": "https://example.test/pr/127",
                    "PrHeadSha": "commit-sha",
                },
            )
            (current / "local-check.log").write_text(
                "tests failed\nAuthorization: Bearer super-secret-token\n",
                encoding="utf-8",
            )

            payload, path = non_success_report.update_report(
                repo,
                {
                    "state": "FAILED",
                    "failed_stage": "local-check",
                    "reason": "local check failed; token=another-secret",
                    "failure_classification": workflow_stages.FAILURE_DETERMINISTIC,
                    "failure_fingerprint": "fingerprint-1",
                },
            )

            self.assertEqual(payload["state"], "FAILED")
            self.assertEqual(path, non_success_report.REPORT_RELATIVE)
            self.assertEqual(payload["non_success_report"], non_success_report.REPORT_RELATIVE)
            report = (current / non_success_report.REPORT_NAME).read_text(encoding="utf-8")
            self.assertIn("## Outcome", report)
            self.assertIn("## What succeeded", report)
            self.assertIn("## What prevented completion", report)
            self.assertIn("## Next steps", report)
            self.assertIn("## Evidence", report)
            self.assertIn("Do not retry the unchanged run", report)
            self.assertIn("<redacted>", report)
            self.assertNotIn("super-secret-token", report)
            self.assertNotIn("another-secret", report)

    def test_waiting_report_says_nothing_has_failed_and_gives_resume_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            state = {
                "IssueNumber": 126,
                "BranchName": "autodev/issue-126",
                "LastLocalCheckPassed": True,
                "LastSemanticVerdict": "pass",
                "LastCommitSha": "head-sha",
                "PrUrl": "https://example.test/pr/126",
                "PrHeadSha": "head-sha",
                "CiProof": {
                    "head_sha": "head-sha",
                    "state": "queued/in-progress",
                    "polls": 12,
                    "checks": [
                        {"name": "buildAndTestGUI", "bucket": "pending", "state": "IN_PROGRESS"},
                        {"name": "unit", "bucket": "pass", "state": "SUCCESS"},
                    ],
                },
            }
            workflow_stages.write_json(current / "state.json", state)
            workflow_stages.write_json(current / "ci-summary.json", state["CiProof"])

            payload, _ = non_success_report.update_report(
                repo,
                {
                    "state": "WAITING",
                    "waiting_reason": "ci-pending",
                    "pr_head_sha": "head-sha",
                },
            )

            report = (current / non_success_report.REPORT_NAME).read_text(encoding="utf-8")
            self.assertEqual(payload["state"], "WAITING")
            self.assertIn("No code failure has been established", report)
            self.assertIn("coordinate --resume", report)
            self.assertIn("This does not mean the implementation failed", report)
            self.assertIn("buildAndTestGUI: IN_PROGRESS", report)

    def test_success_clears_stale_non_success_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            path = current / non_success_report.REPORT_NAME
            path.write_text("stale\n", encoding="utf-8")

            payload, report_path = non_success_report.update_report(repo, {"state": "PR_READY"})

            self.assertEqual(payload["state"], "PR_READY")
            self.assertEqual(report_path, "")
            self.assertFalse(path.exists())

    def test_report_generation_failure_never_replaces_primary_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            workflow_stages.write_json(current / "state.json", {"IssueNumber": 127})

            with patch("automation.non_success_report._write_atomic", side_effect=OSError("disk failed")):
                payload, report_path = non_success_report.update_report(
                    repo,
                    {"state": "BLOCKED", "reason": "primary blocker"},
                )

            self.assertEqual(payload["state"], "BLOCKED")
            self.assertEqual(payload["reason"], "primary blocker")
            self.assertEqual(report_path, "")
            self.assertIn("disk failed", str(payload["non_success_report_error"]))


if __name__ == "__main__":
    unittest.main()

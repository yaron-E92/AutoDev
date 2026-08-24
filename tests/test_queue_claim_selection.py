from __future__ import annotations

from automation import queue_contract

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import queue_selection


class QueueClaimSelectionTests(unittest.TestCase):
    @staticmethod
    def _state(number: int, created_at: str) -> queue_contract.QueueState:
        return queue_contract.QueueState(
            issue=queue_contract.QueueIssue(
                number=number,
                title=f"Issue {number}",
                url=f"https://github.test/owner/repo/issues/{number}",
                state="open",
                labels=(queue_contract.MANAGED_LABEL, queue_contract.READY_LABEL),
                created_at=created_at,
            ),
            reason="ready",
        )

    def test_claim_exclusion_skips_owned_issue_without_changing_ranking(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            states = [
                self._state(1, "2026-01-01T00:00:00Z"),
                self._state(2, "2026-01-02T00:00:00Z"),
                self._state(3, "2026-01-03T00:00:00Z"),
            ]
            with patch.object(
                queue_selection,
                "reconcile_queue",
                return_value=(states, ()),
            ), patch.object(
                queue_selection,
                "active_autodev_prs",
                return_value={},
            ), patch.object(
                queue_selection,
                "load_roadmap",
                return_value=queue_selection.Roadmap(),
            ):
                result = queue_selection.select_next(
                    repo,
                    "owner/repo",
                    existing_run_inspector=lambda _repo: queue_selection.ExistingRun("NONE"),
                    excluded_issue_numbers={1},
                )

            self.assertEqual(result.state, "SELECTED")
            self.assertEqual(result.issue_number, 2)
            self.assertEqual(result.source, "oldest")
            self.assertTrue(
                any("#1" in item and "distributed claim" in item for item in result.ineligible)
            )

    def test_existing_durable_run_still_precedes_claim_exclusions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            states = [self._state(42, "2026-01-01T00:00:00Z")]
            with patch.object(
                queue_selection,
                "reconcile_queue",
                return_value=(states, ()),
            ):
                result = queue_selection.select_next(
                    repo,
                    "owner/repo",
                    existing_run_inspector=lambda _repo: queue_selection.ExistingRun(
                        "RESUME_EXISTING",
                        issue_number=42,
                        branch="autodev/issue-42-work",
                        next_stage="semantic",
                        next_action="verifier",
                        reason="resume first",
                    ),
                    excluded_issue_numbers={42},
                )

            self.assertEqual(result.state, "RESUME_EXISTING")
            self.assertEqual(result.issue_number, 42)
            self.assertEqual(result.source, "existing-run")


if __name__ == "__main__":
    unittest.main()

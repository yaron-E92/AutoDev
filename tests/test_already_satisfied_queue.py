from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import queue_classification, queue_contract, queue_workflow


class AlreadySatisfiedQueueTests(unittest.TestCase):
    def _done_issue(self) -> queue_contract.QueueIssue:
        return queue_contract.QueueIssue(
            number=179,
            title="Use Artifacts output",
            url="https://github.test/owner/repo/issues/179",
            state="open",
            labels=(
                queue_contract.MANAGED_LABEL,
                queue_contract.DONE_LABEL,
                queue_contract.READY_LABEL,
            ),
        )

    def test_managed_done_issue_is_not_ready_even_when_open(self) -> None:
        state = queue_classification.classify_issue(
            self._done_issue(),
            [],
            queue_contract.QueuePolicy(),
        )

        self.assertEqual(state.reason, "done")
        self.assertNotEqual(state.reason, "ready")

    def test_queue_inspection_keeps_done_issue_visible_but_non_runnable(self) -> None:
        issue = self._done_issue()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            queue_workflow,
            "list_issues",
            return_value=[issue],
        ), patch.object(
            queue_workflow,
            "list_blockers",
            return_value=[],
        ):
            states = queue_workflow.inspect_queue(
                Path(temp_dir),
                "owner/repo",
            )

        self.assertEqual(len(states), 1)
        self.assertEqual(states[0].reason, "done")

    def test_done_state_removes_stale_ready_and_blocked_derivations(self) -> None:
        state = queue_classification.classify_issue(
            self._done_issue(),
            [],
            queue_contract.QueuePolicy(),
        )
        runner_calls: list[list[str]] = []

        def runner(argv, **_kwargs):
            runner_calls.append(list(argv))
            class Result:
                returncode = 0
                stdout = ""
                stderr = ""
            return Result()

        changed = queue_classification._update_derived_labels(
            Path("."),
            "owner/repo",
            state,
            runner=runner,
        )

        self.assertTrue(changed)
        command = runner_calls[-1]
        self.assertIn("--remove-label", command)
        self.assertIn(queue_contract.READY_LABEL, command)
        self.assertNotIn("--add-label", command)


if __name__ == "__main__":
    unittest.main()

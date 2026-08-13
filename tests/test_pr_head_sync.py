from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import pr_head_sync, workflow_stages


class PrHeadSyncTests(unittest.TestCase):
    def _state(self) -> dict[str, object]:
        return {
            "CreatedParentSha": "old-head",
            "CreatedCommitSha": "new-head",
            "LastCommitSha": "new-head",
        }

    def _current(self, root: Path, state: dict[str, object]) -> Path:
        current = root / ".autodev-run" / "current"
        current.mkdir(parents=True)
        workflow_stages.write_state(current, state)
        return current

    def test_stale_previous_head_is_retried_until_new_head_converges(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            state = self._state()
            current = self._current(repo, state)
            calls: list[int] = []

            def original(repo_arg, current_arg, state_arg, *, runner):
                calls.append(1)
                if len(calls) < 3:
                    raise workflow_stages.WorkflowStageError(
                        "PR head old-head does not match the exact AutoDev commit new-head"
                    )

            with patch.dict(
                os.environ,
                {"PR_HEAD_SYNC_ATTEMPTS": "3", "PR_HEAD_SYNC_SECONDS": "0"},
                clear=False,
            ):
                pr_head_sync.ensure_pr_with_convergence(
                    original,
                    repo,
                    current,
                    state,
                    runner=lambda *args, **kwargs: None,
                )

            self.assertEqual(len(calls), 3)

    def test_permanent_previous_head_mismatch_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            state = self._state()
            current = self._current(repo, state)
            calls: list[int] = []

            def original(repo_arg, current_arg, state_arg, *, runner):
                calls.append(1)
                raise workflow_stages.WorkflowStageError(
                    "PR head old-head does not match the exact AutoDev commit new-head"
                )

            with patch.dict(
                os.environ,
                {"PR_HEAD_SYNC_ATTEMPTS": "2", "PR_HEAD_SYNC_SECONDS": "0"},
                clear=False,
            ):
                with self.assertRaisesRegex(workflow_stages.WorkflowStageError, "old-head"):
                    pr_head_sync.ensure_pr_with_convergence(
                        original,
                        repo,
                        current,
                        state,
                        runner=lambda *args, **kwargs: None,
                    )

            self.assertEqual(len(calls), 2)

    def test_unexpected_head_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            state = self._state()
            current = self._current(repo, state)
            calls: list[int] = []

            def original(repo_arg, current_arg, state_arg, *, runner):
                calls.append(1)
                raise workflow_stages.WorkflowStageError(
                    "PR head unrelated-head does not match the exact AutoDev commit new-head"
                )

            with patch.dict(os.environ, {"PR_HEAD_SYNC_ATTEMPTS": "4"}, clear=False):
                with self.assertRaisesRegex(workflow_stages.WorkflowStageError, "unrelated-head"):
                    pr_head_sync.ensure_pr_with_convergence(
                        original,
                        repo,
                        current,
                        state,
                        runner=lambda *args, **kwargs: None,
                    )

            self.assertEqual(len(calls), 1)

    def test_non_head_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            state = self._state()
            current = self._current(repo, state)
            calls: list[int] = []

            def original(repo_arg, current_arg, state_arg, *, runner):
                calls.append(1)
                raise workflow_stages.WorkflowStageError("gh pr view failed: HTTP 500")

            with patch.dict(os.environ, {"PR_HEAD_SYNC_ATTEMPTS": "4"}, clear=False):
                with self.assertRaisesRegex(workflow_stages.WorkflowStageError, "HTTP 500"):
                    pr_head_sync.ensure_pr_with_convergence(
                        original,
                        repo,
                        current,
                        state,
                        runner=lambda *args, **kwargs: None,
                    )

            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()

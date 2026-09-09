from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation import (
    already_satisfied,
    already_satisfied_hooks,
    role_coordinator_flow,
    workflow_preparation,
)
from automation.workflow_storage import write_json
from tests import test_already_satisfied


class AlreadySatisfiedBoundaryTests(unittest.TestCase):
    def test_confirmed_candidate_stops_before_implementer_runtime(self) -> None:
        fixture = test_already_satisfied.AlreadySatisfiedTests()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current = fixture._repo(Path(temp_dir))
            already_satisfied_hooks.install()
            runtime = Mock()
            snapshots: dict[str, object] = {}

            with patch.object(
                already_satisfied,
                "probe_candidate",
                return_value="confirmed",
            ) as probe:
                with self.assertRaises(already_satisfied_hooks.AlreadySatisfiedComplete):
                    role_coordinator_flow.run_role(
                        repo,
                        "implementer",
                        runtime,
                        snapshots,
                    )

            probe.assert_called_once()
            runtime.invoke.assert_not_called()

    def test_explicit_rerun_of_already_satisfied_issue_refetches_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            write_json(
                current / "state.json",
                {
                    "IssueNumber": 179,
                    "Status": "AlreadySatisfied",
                },
            )

            with patch.object(
                workflow_preparation.repository_identity,
                "resolve_github_repository",
                return_value="yaron-E92/events",
            ), patch.object(
                workflow_preparation,
                "gh_json",
                side_effect=RuntimeError("remote issue refetched"),
            ) as gh_json:
                with self.assertRaisesRegex(RuntimeError, "remote issue refetched"):
                    workflow_preparation.ensure_prepared_issue(repo, "179")

            self.assertEqual(gh_json.call_count, 1)
            arguments = gh_json.call_args.args[1]
            self.assertEqual(arguments[:3], ["issue", "view", "179"])

    def test_nonterminal_same_issue_still_reuses_prepared_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            write_json(
                current / "state.json",
                {
                    "IssueNumber": 179,
                    "Status": "Planned",
                },
            )

            with patch.object(
                workflow_preparation.development_policy,
                "assert_resume_compatible",
            ) as compatible, patch.object(
                workflow_preparation,
                "gh_json",
            ) as gh_json:
                result = workflow_preparation.ensure_prepared_issue(repo, "179")

            self.assertEqual(result, current)
            compatible.assert_called_once()
            gh_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()

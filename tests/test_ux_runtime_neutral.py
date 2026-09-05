from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import (
    opencode_adapter_contract,
    role_coordinator_runtime,
    role_runtime,
    ux_role_context,
    workflow_stages,
)


class RecordingRuntime:
    name = "recording"

    def __init__(self) -> None:
        self.invocations: list[role_runtime.RoleInvocationContext] = []

    def invoke(self, context: role_runtime.RoleInvocationContext, *, runner, which=None):
        self.invocations.append(context)
        return role_runtime.RoleInvocationResult(
            runtime=self.name,
            role=context.role,
            phase=context.phase,
            returncode=0,
            elapsed_ms=1,
        )


class RuntimeNeutralUXContextTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        (current / "state.json").write_text(
            json.dumps({"IssueText": "Fallback issue text"}),
            encoding="utf-8",
        )
        (current / "issue.md").write_text("Implement task-editor UX\n", encoding="utf-8")
        return repo

    def test_ux_context_is_supplied_through_generic_runtime_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            runtime = RecordingRuntime()
            ux_prompt = "\n\n# Pinned UX authority\n\nnavigation: task-first\n"

            with patch.object(
                role_coordinator_runtime,
                "_prepare_role",
            ), patch.object(
                role_coordinator_runtime.ux_role_context,
                "prepare_role_context",
                return_value=(ux_prompt, {"ux_context_fingerprint": "abc"}),
            ) as prepare_ux, patch.object(
                role_coordinator_runtime,
                "_role_output_path",
                return_value=None,
            ), patch.object(
                role_coordinator_runtime,
                "_accept_role",
                return_value=[],
            ), patch.object(
                role_coordinator_runtime,
                "_record_attempt",
                return_value="",
            ), patch.object(
                role_coordinator_runtime,
                "role_acceptance",
                return_value={"state": "ACCEPTED", "role": "planner"},
            ):
                role_coordinator_runtime.run_role(
                    repo,
                    "planner",
                    runtime,
                    snapshots={},
                    already_prepared=True,
                )

            self.assertEqual(len(runtime.invocations), 1)
            invocation = runtime.invocations[0]
            self.assertEqual(invocation.role, "planner")
            self.assertIn("# Pinned UX authority", invocation.prompt)
            self.assertIn("navigation: task-first", invocation.prompt)
            prepare_ux.assert_called_once_with(
                repo,
                repo / workflow_stages.CURRENT_DIR,
                "planner",
                "Implement task-editor UX\n",
            )

    def test_protocol_correction_keeps_same_runtime_neutral_ux_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / workflow_stages.CURRENT_DIR
            (current / "contract-correction-planner.md").write_text(
                "Correct the planner output.\n",
                encoding="utf-8",
            )
            runtime = RecordingRuntime()
            ux_prompt = "\n\n# Pinned UX authority\n\nnavigation: task-first\n"

            with patch.object(
                role_coordinator_runtime,
                "_prepare_role",
            ), patch.object(
                role_coordinator_runtime.ux_role_context,
                "prepare_role_context",
                return_value=(ux_prompt, {"ux_context_fingerprint": "abc"}),
            ), patch.object(
                role_coordinator_runtime,
                "_role_output_path",
                return_value=None,
            ), patch.object(
                role_coordinator_runtime,
                "_accept_role",
                side_effect=[
                    opencode_adapter_contract.OpenCodeAdapterError("invalid planner output"),
                    [],
                ],
            ), patch.object(
                role_coordinator_runtime,
                "_record_attempt",
                return_value="attempt.json",
            ), patch.object(
                role_coordinator_runtime,
                "role_acceptance",
                return_value={"state": "ACCEPTED", "role": "planner"},
            ):
                role_coordinator_runtime.run_role(
                    repo,
                    "planner",
                    runtime,
                    snapshots={},
                    already_prepared=True,
                )

            self.assertEqual(len(runtime.invocations), 2)
            self.assertEqual(runtime.invocations[0].phase, "work")
            self.assertEqual(runtime.invocations[1].phase, "correction")
            for invocation in runtime.invocations:
                self.assertIn("# Pinned UX authority", invocation.prompt)
                self.assertIn("navigation: task-first", invocation.prompt)

    def test_ux_context_failure_is_runtime_neutral_setup_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            runtime = RecordingRuntime()

            with patch.object(
                role_coordinator_runtime,
                "_prepare_role",
            ), patch.object(
                role_coordinator_runtime.ux_role_context,
                "prepare_role_context",
                side_effect=ux_role_context.UXRoleContextError("selected UX input is missing"),
            ):
                with self.assertRaises(role_coordinator_runtime.RoleCoordinatorError) as raised:
                    role_coordinator_runtime.run_role(
                        repo,
                        "planner",
                        runtime,
                        snapshots={},
                        already_prepared=True,
                    )

            self.assertEqual(raised.exception.classification, "setup/configuration")
            self.assertIn("selected UX input is missing", str(raised.exception))
            self.assertEqual(runtime.invocations, [])


if __name__ == "__main__":
    unittest.main()

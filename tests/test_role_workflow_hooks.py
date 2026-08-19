from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import (
    opencode_resume,
    role_coordinator,
    role_resume,
    role_workflow_hooks,
    semantic_repair_budget,
    windows_verification,
    workflow_stages,
)


class RoleWorkflowHookTests(unittest.TestCase):
    def test_ci_waiting_returns_waiting_payload_from_generic_coordinate(self):
        waiting = {
            "state": "WAITING",
            "failed_stage": "pr-and-ci",
            "reason": "required CI is still running",
            "waiting_reason": "ci-pending",
        }

        def base_run_stage(*args, **kwargs):
            return dict(waiting)

        def base_coordinate(*args, **kwargs):
            role_coordinator.run_stage(
                Path("."),
                "pr-and-ci",
                runtime_name="mock",
            )
            self.fail("WAITING must stop the deterministic transition loop")

        with patch.object(role_coordinator, "run_stage", base_run_stage), patch.object(
            role_coordinator,
            "coordinate",
            base_coordinate,
        ):
            role_workflow_hooks._install_waiting_bridge()
            payload = role_coordinator.coordinate(Path("."))

        self.assertEqual(payload, waiting)

    def test_resume_bridge_reopens_semantic_budget_and_surfaces_windows_metadata(self):
        base_payload = {
            "state": "RESUME",
            "next_action": "pr-and-ci",
            "next_stage": "semantic-verified",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)

            def base_resume(*args, **kwargs):
                return dict(base_payload)

            with patch.object(role_resume, "resume", base_resume), patch.object(
                semantic_repair_budget,
                "maybe_reopen_exhausted_budget",
            ) as reopen, patch.object(
                semantic_repair_budget,
                "_append_resume_metadata",
            ) as append, patch.object(
                workflow_stages,
                "read_state",
                return_value={"DeferredVerificationObligations": [{"platform": "windows"}]},
            ), patch.object(
                role_workflow_hooks.run_manifest,
                "load_manifest",
                return_value={},
            ), patch.object(
                opencode_resume,
                "repair_attempts",
                return_value={"local": 0, "semantic": 0, "ci": 0, "windows": 2},
            ), patch.object(
                windows_verification,
                "payload_metadata",
                return_value={"windows_verification_required": True},
            ), patch.object(
                windows_verification,
                "windows_required",
                return_value=True,
            ), patch.object(
                windows_verification,
                "proof_current",
                return_value=False,
            ):
                role_workflow_hooks._install_resume_bridge()
                payload = role_resume.resume(repo, {})

            reopen.assert_called_once_with(repo.resolve())
            append.assert_called_once_with(repo.resolve(), payload)
            self.assertEqual(payload["windows_repair_attempt"], 2)
            self.assertTrue(payload["windows_verification_required"])
            self.assertEqual(
                payload["next_stage"],
                windows_verification.MANIFEST_STAGE,
            )

    def test_install_extends_generic_repair_vocabulary_with_windows(self):
        original = dict(role_coordinator.REPAIR_KINDS)
        try:
            role_coordinator.REPAIR_KINDS.pop("fixer-windows", None)
            with patch.object(
                role_workflow_hooks,
                "_install_waiting_bridge",
            ), patch.object(
                role_workflow_hooks,
                "_install_resume_bridge",
            ):
                role_workflow_hooks.install()
            self.assertEqual(
                role_coordinator.REPAIR_KINDS["fixer-windows"],
                "windows",
            )
        finally:
            role_coordinator.REPAIR_KINDS.clear()
            role_coordinator.REPAIR_KINDS.update(original)


if __name__ == "__main__":
    unittest.main()

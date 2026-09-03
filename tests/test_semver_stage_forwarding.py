from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import (
    context_optimization,
    execution_classification_hooks,
    opencode_adapter_roles,
    windows_workflow_hooks,
    workflow_stages,
)


class SemVerStageForwardingTests(unittest.TestCase):
    def test_workflow_stage_facade_forwards_semver_intent_override(self) -> None:
        original_installed = workflow_stages._POLICY_HOOKS_INSTALLED
        original_executor = workflow_stages._WORKFLOW_EXECUTOR
        seen: list[str] = []

        def executor(name, repo, **kwargs):
            seen.append(str(kwargs.get("semver_intent_override", "")))
            return 0, {"state": "CONTINUE"}

        try:
            workflow_stages._POLICY_HOOKS_INSTALLED = True
            workflow_stages._WORKFLOW_EXECUTOR = executor
            for intent in ("", "patch"):
                with self.subTest(intent=intent or "<default>"):
                    code, _ = workflow_stages.execute_stage(
                        "preflight",
                        Path("."),
                        semver_intent_override=intent,
                    )
                    self.assertEqual(code, 0)
            self.assertEqual(seen, ["", "patch"])
        finally:
            workflow_stages._POLICY_HOOKS_INSTALLED = original_installed
            workflow_stages._WORKFLOW_EXECUTOR = original_executor

    def test_windows_wrapper_forwards_semver_intent_override(self) -> None:
        seen: list[str] = []

        def original(name, repo, **kwargs):
            seen.append(str(kwargs.get("semver_intent_override", "")))
            return 0, {"state": "CONTINUE"}

        core = SimpleNamespace(
            AUTODEV_ROOT=Path("."),
            CURRENT_DIR=Path(".autodev-run") / "current",
            subprocess=SimpleNamespace(run=lambda *args, **kwargs: None),
            shutil=SimpleNamespace(which=lambda name: None),
        )
        execute_stage = windows_workflow_hooks.build_execute_stage(core, original)

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            for intent in ("", "minor"):
                with self.subTest(intent=intent or "<default>"):
                    code, _ = execute_stage(
                        "noop",
                        repo,
                        semver_intent_override=intent,
                    )
                    self.assertEqual(code, 0)

        self.assertEqual(seen, ["", "minor"])

    def test_installed_context_and_classification_hooks_preserve_override(self) -> None:
        original_execute = workflow_stages.execute_stage
        original_prepare = opencode_adapter_roles.prepare_role
        original_context_installed = context_optimization._INSTALLED
        seen: list[str] = []

        def base_execute(name, repo, **kwargs):
            seen.append(str(kwargs.get("semver_intent_override", "")))
            return 0, {"state": "IGNORED"}

        try:
            workflow_stages.execute_stage = base_execute
            context_optimization._INSTALLED = False
            context_optimization.install()
            execution_classification_hooks._install_prepare_gate()

            for intent in ("", "none"):
                with self.subTest(intent=intent or "<default>"):
                    code, _ = workflow_stages.execute_stage(
                        "preflight",
                        Path("."),
                        semver_intent_override=intent,
                    )
                    self.assertEqual(code, 0)

            self.assertEqual(seen, ["", "none"])
        finally:
            workflow_stages.execute_stage = original_execute
            opencode_adapter_roles.prepare_role = original_prepare
            context_optimization._INSTALLED = original_context_installed


if __name__ == "__main__":
    unittest.main()

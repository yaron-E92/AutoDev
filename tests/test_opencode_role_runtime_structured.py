from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_role_runtime,
    opencode_structured_output,
    role_output_contract,
    role_runtime,
)


class _Completed:
    returncode = 0
    stdout = "{}"
    stderr = ""


class OpenCodeRoleRuntimeStructuredTests(unittest.TestCase):
    def _runtime(self) -> opencode_role_runtime.OpenCodeRoleRuntime:
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        runtime._mappings = {
            "verifier": {
                "agent": "autodev-verifier",
                "model": "openai/test-model",
                "source": "autodev-profile:user:mixed",
                "inherits_from": "",
            }
        }
        return runtime

    def _context(self, repo: Path) -> role_runtime.RoleInvocationContext:
        return role_runtime.RoleInvocationContext(
            repo=repo,
            role="verifier",
            prompt="verify this change",
            output_contract=role_output_contract.contract_for_role("verifier"),
            ux_context_fingerprint="abc123",
        )

    def test_native_success_materializes_existing_verifier_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            payload = {
                "verdict": "pass",
                "requirements": [],
                "findings": [],
                "repair_brief": "",
            }
            with patch.object(
                opencode_role_runtime.opencode_cli,
                "resolve_opencode_cli",
                return_value="opencode",
            ), patch.object(
                opencode_role_runtime.privacy,
                "load_policy",
                return_value=SimpleNamespace(enabled=False),
            ), patch.object(
                opencode_role_runtime.opencode_structured_output,
                "invoke",
                return_value=opencode_structured_output.NativeStructuredOutput(payload),
            ) as native:
                result = self._runtime().invoke(
                    self._context(repo), runner=lambda *a, **k: _Completed()
                )

            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.structured_output_mode, "native-validated")
            self.assertEqual(result.structured_output_state, "schema-validated")
            self.assertEqual(result.contract_name, "autodev.semantic-verifier")
            artifact = repo / ".autodev-run" / "current" / "verification-result.json"
            self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), payload)
            self.assertEqual(native.call_args.kwargs["model"], "openai/test-model")

    def test_unavailable_native_api_falls_back_to_existing_cli_text_protocol(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return _Completed()

            with patch.object(
                opencode_role_runtime.opencode_cli,
                "resolve_opencode_cli",
                return_value="opencode",
            ), patch.object(
                opencode_role_runtime.privacy,
                "load_policy",
                return_value=SimpleNamespace(enabled=False),
            ), patch.object(
                opencode_role_runtime.opencode_structured_output,
                "invoke",
                side_effect=opencode_structured_output.StructuredOutputUnavailable("old runtime"),
            ):
                result = self._runtime().invoke(self._context(repo), runner=runner)

            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(result.structured_output_state, "native-unavailable")
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][1], "run")
            self.assertIn("--format", commands[0])
            self.assertNotIn("--schema", commands[0])

    def test_native_schema_exhaustion_does_not_consume_cli_correction_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            cli_calls = 0

            def runner(command, **kwargs):
                nonlocal cli_calls
                cli_calls += 1
                return _Completed()

            with patch.object(
                opencode_role_runtime.opencode_cli,
                "resolve_opencode_cli",
                return_value="opencode",
            ), patch.object(
                opencode_role_runtime.privacy,
                "load_policy",
                return_value=SimpleNamespace(enabled=False),
            ), patch.object(
                opencode_role_runtime.opencode_structured_output,
                "invoke",
                side_effect=opencode_structured_output.StructuredOutputExhausted(
                    "schema failed", retries=2
                ),
            ):
                result = self._runtime().invoke(self._context(repo), runner=runner)

            self.assertEqual(result.termination, "structured-output-exhausted")
            self.assertEqual(result.schema_retry_count, 2)
            self.assertEqual(result.structured_output_state, "schema-exhausted")
            self.assertEqual(cli_calls, 0)


if __name__ == "__main__":
    unittest.main()

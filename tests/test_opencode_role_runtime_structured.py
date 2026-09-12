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
            role: {
                "agent": f"autodev-{role}",
                "model": "openai/test-model",
                "source": "autodev-profile:user:mixed",
                "inherits_from": "",
            }
            for role in ("verifier", "planner", "synthesizer", "implementer")
        }
        return runtime

    def _context(
        self,
        repo: Path,
        role: str = "verifier",
    ) -> role_runtime.RoleInvocationContext:
        return role_runtime.RoleInvocationContext(
            repo=repo,
            role=role,
            prompt=f"run {role}",
            output_contract=role_output_contract.contract_for_role(role),
            ux_context_fingerprint="abc123",
        )

    def _invoke_native(self, repo: Path, role: str, payload: dict[str, object]):
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
                self._context(repo, role), runner=lambda *a, **k: _Completed()
            )
        return result, native

    def test_native_success_materializes_existing_verifier_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            payload = {
                "verdict": "pass",
                "requirements": [],
                "findings": [],
                "repair_brief": "",
            }
            result, native = self._invoke_native(repo, "verifier", payload)

            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.structured_output_mode, "native-validated")
            self.assertEqual(result.structured_output_state, "schema-validated")
            self.assertEqual(result.contract_name, "autodev.semantic-verifier")
            artifact = repo / ".autodev-run" / "current" / "verification-result.json"
            self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), payload)
            self.assertEqual(native.call_args.kwargs["model"], "openai/test-model")

    def test_native_success_materializes_planner_and_synthesizer_protocol_artifacts(self):
        cases = {
            "planner": (
                {
                    "plan": {
                        "where_to_look": "automation/",
                        "files_areas_likely_to_touch": "automation/runtime.py",
                        "assumptions": "",
                        "implementation_plan": "Implement the runtime-neutral contract.",
                        "risks_gotchas": "",
                        "recommended_implementation_approach": "Keep fallback behavior.",
                    },
                    "ux": {"constraints_addressed": [], "open_questions": []},
                },
                "plan.md",
                "1) Where to look",
            ),
            "synthesizer": (
                {
                    "handoff_markdown": "# Bounded handoff\n\nUse the runtime seam.",
                    "ux": {"constraints_addressed": [], "open_questions": []},
                },
                "synthesized-handoff.md",
                "# Bounded handoff",
            ),
        }
        for role, (payload, artifact_name, marker) in cases.items():
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                result, native = self._invoke_native(repo, role, payload)
                self.assertEqual(result.termination, "completed")
                self.assertEqual(result.structured_output_mode, "native-validated")
                self.assertEqual(result.structured_output_state, "schema-validated")
                artifact = repo / ".autodev-run" / "current" / artifact_name
                self.assertIn(marker, artifact.read_text(encoding="utf-8"))
                self.assertEqual(native.call_args.kwargs["role"], role)

    def test_unavailable_native_api_falls_back_to_existing_cli_text_protocol(self):
        for role in ("verifier", "planner", "synthesizer"):
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp_dir:
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
                    result = self._runtime().invoke(self._context(repo, role), runner=runner)

                self.assertEqual(result.termination, "completed")
                self.assertEqual(result.structured_output_mode, "fallback-text")
                self.assertEqual(result.structured_output_state, "native-unavailable")
                self.assertEqual(len(commands), 1)
                self.assertEqual(commands[0][1], "run")
                self.assertIn("--format", commands[0])
                self.assertNotIn("--schema", commands[0])

    def test_native_schema_exhaustion_uses_one_fallback_for_supported_contract(self):
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
            ) as native:
                result = self._runtime().invoke(self._context(repo), runner=runner)

            self.assertEqual(native.call_count, 1)
            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.schema_retry_count, 2)
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(cli_calls, 1)

    def test_native_schema_exhaustion_stays_terminal_without_safe_fallback_contract(self):
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
            ) as native:
                result = self._runtime().invoke(
                    self._context(repo, "implementer"), runner=runner
                )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(result.termination, "structured-output-exhausted")
            self.assertEqual(result.schema_retry_count, 2)
            self.assertEqual(result.structured_output_state, "schema-exhausted")
            self.assertEqual(cli_calls, 0)


if __name__ == "__main__":
    unittest.main()

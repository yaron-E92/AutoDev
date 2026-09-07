from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    execution_classification_reader_advisory,
    opencode_adapter_handoff,
    opencode_role_runtime,
    opencode_structured_output,
    role_coordinator_runtime,
    role_output_contract,
    role_runtime,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation.role_coordinator_contract import RoleCoordinatorError


class _Completed:
    def __init__(self, returncode: int = 0, stdout: str = "{}", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ReaderStructuredOutputHotfixTests(unittest.TestCase):
    def _runtime(self) -> opencode_role_runtime.OpenCodeRoleRuntime:
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        runtime._mappings = {
            "reader": {
                "agent": "autodev-reader",
                "model": "ollama/gpt-oss:20b-autodev",
                "source": "test",
                "inherits_from": "",
            },
            "verifier": {
                "agent": "autodev-verifier",
                "model": "openai/test-model",
                "source": "test",
                "inherits_from": "",
            },
        }
        return runtime

    def _context(self, repo: Path, role: str = "reader") -> role_runtime.RoleInvocationContext:
        return role_runtime.RoleInvocationContext(
            repo=repo,
            role=role,
            prompt=f"run {role}",
            output_contract=role_output_contract.contract_for_role(role),
        )

    def _invoke_with_native(self, runtime, context, native_outcome, runner):
        native_patch = (
            {"side_effect": native_outcome}
            if isinstance(native_outcome, BaseException)
            else {"return_value": native_outcome}
        )
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
            **native_patch,
        ) as native:
            result = runtime.invoke(
                context,
                runner=runner,
                which=lambda _name: "opencode",
            )
        return result, native

    def test_reader_schema_requires_only_factual_handoff(self):
        contract = role_output_contract.contract_for_role("reader")
        assert contract is not None
        schema = contract.schema()

        self.assertEqual(schema["required"], ["handoff_markdown"])
        self.assertNotIn("required", schema["properties"]["ux"])

    def test_minimal_reader_payload_normalizes_optional_evidence_and_ux(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            contract = role_output_contract.contract_for_role("reader")
            assert contract is not None

            target = role_output_contract.materialize_structured_output(
                repo,
                contract,
                {"handoff_markdown": "Reader facts only."},
            )
            assert target is not None
            current = repo / workflow_stages.CURRENT_DIR

            evidence = json.loads(
                (current / "structured-reader-evidence.json").read_text(encoding="utf-8")
            )
            self.assertEqual(evidence, [])

            execution_classification_reader_advisory._normalize_structured_reader_ux(current)
            ux = json.loads(
                (current / "structured-ux-reader.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                ux,
                {"constraints_addressed": [], "open_questions": []},
            )
            role_output_contract.validate_materialized_ux_sidecar(current, "reader")

    def test_optional_reader_ux_reference_still_requires_effective_ux_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            contract = role_output_contract.contract_for_role("reader")
            assert contract is not None
            role_output_contract.materialize_structured_output(
                repo,
                contract,
                {
                    "handoff_markdown": "Reader facts.",
                    "ux": {
                        "constraints_addressed": [
                            {
                                "source_kind": "state",
                                "source_id": "task-editor-empty",
                                "impact": "Preserve the selected empty state.",
                            }
                        ]
                    },
                },
            )
            current = repo / workflow_stages.CURRENT_DIR
            execution_classification_reader_advisory._normalize_structured_reader_ux(current)

            with self.assertRaises(role_output_contract.RoleOutputContractError) as raised:
                role_output_contract.validate_materialized_ux_sidecar(current, "reader")
            self.assertIn("no effective AutoDev UX context", str(raised.exception))

    def test_reader_control_plane_fields_remain_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            contract = role_output_contract.contract_for_role("reader")
            assert contract is not None
            with self.assertRaises(role_output_contract.RoleOutputContractError):
                role_output_contract.materialize_structured_output(
                    repo,
                    contract,
                    {
                        "handoff_markdown": "Reader facts.",
                        "execution_classification": "manual-external",
                    },
                )

    def test_native_reader_succeeds_with_handoff_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = self._runtime()
            context = self._context(repo)
            native_value = opencode_structured_output.NativeStructuredOutput(
                {"handoff_markdown": "Minimal native Reader handoff."}
            )

            result, native = self._invoke_with_native(
                runtime,
                context,
                native_value,
                lambda *args, **kwargs: _Completed(),
            )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.structured_output_mode, "native-validated")
            self.assertEqual(result.structured_output_state, "schema-validated")
            self.assertIn(
                "Minimal native Reader handoff",
                (repo / workflow_stages.CURRENT_DIR / "reader-brief.md").read_text(
                    encoding="utf-8"
                ),
            )

    def test_events_182_shape_schema_exhaustion_uses_exactly_one_text_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / workflow_stages.DIAGNOSTICS_FILE).write_text("{}\n", encoding="utf-8")
            runtime = self._runtime()
            context = self._context(repo)
            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                (current / "reader-brief.md").write_text(
                    "# Reader handoff\n\nRepository facts from the local model.\n",
                    encoding="utf-8",
                )
                return _Completed()

            exhausted = opencode_structured_output.StructuredOutputExhausted(
                "StructuredOutputError",
                retries=2,
            )
            result, native = self._invoke_with_native(
                runtime,
                context,
                exhausted,
                runner,
            )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0][1], "run")
            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.READER_SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(result.schema_retry_count, 2)
            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(
                int((diagnostics.get("protocol_correction_attempts", {}) or {}).get("reader", 0)),
                0,
            )

            synthesis = opencode_adapter_handoff._prepare_synthesizer(
                current,
                "Events #182",
            )
            self.assertIn("Repository facts from the local model", synthesis)

    def test_failed_text_fallback_terminates_once_as_role_protocol_exhausted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / workflow_stages.DIAGNOSTICS_FILE).write_text("{}\n", encoding="utf-8")
            runtime = self._runtime()
            context = self._context(repo)
            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return _Completed()

            exhausted = opencode_structured_output.StructuredOutputExhausted(
                "StructuredOutputError",
                retries=2,
            )
            result, native = self._invoke_with_native(
                runtime,
                context,
                exhausted,
                runner,
            )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(len(commands), 1)
            self.assertEqual(result.termination, "structured-output-exhausted")
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.READER_SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(result.schema_retry_count, 2)
            self.assertIn("fallback-text Reader output rejected", result.stderr)

            with self.assertRaises(RoleCoordinatorError) as raised:
                role_coordinator_runtime._runtime_failure(repo, "reader", result)
            self.assertEqual(
                raised.exception.classification,
                role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
            )

            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(
                diagnostics["last_structured_output"]["state"],
                opencode_role_runtime.READER_SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(diagnostics["last_structured_output"]["schema_retry_count"], 2)
            self.assertEqual(
                int((diagnostics.get("protocol_correction_attempts", {}) or {}).get("reader", 0)),
                0,
            )

    def test_non_reader_schema_exhaustion_does_not_gain_reader_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = self._runtime()
            context = self._context(repo, role="verifier")
            commands: list[list[str]] = []

            exhausted = opencode_structured_output.StructuredOutputExhausted(
                "StructuredOutputError",
                retries=2,
            )
            result, native = self._invoke_with_native(
                runtime,
                context,
                exhausted,
                lambda command, **kwargs: commands.append(list(command)) or _Completed(),
            )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(commands, [])
            self.assertEqual(result.termination, "structured-output-exhausted")
            self.assertEqual(result.structured_output_mode, "native-validated")
            self.assertEqual(result.structured_output_state, "schema-exhausted")


if __name__ == "__main__":
    unittest.main()

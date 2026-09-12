from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    execution_classification_reader_advisory,
    opencode_adapter_contract,
    opencode_adapter_handoff,
    opencode_cli_text,
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


def _text_event(text: str, *, synthetic: bool = False, metadata: dict | None = None) -> str:
    part: dict[str, object] = {
        "id": "prt_reader",
        "messageID": "msg_reader",
        "sessionID": "ses_reader",
        "type": "text",
        "text": text,
        "time": {"start": 1, "end": 2},
    }
    if synthetic:
        part["synthetic"] = True
    if metadata is not None:
        part["metadata"] = metadata
    return json.dumps(
        {
            "type": "text",
            "timestamp": 2,
            "sessionID": "ses_reader",
            "part": part,
        }
    )


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

    def _context(
        self,
        repo: Path,
        role: str = "reader",
        *,
        ux_context_fingerprint: str = "",
    ) -> role_runtime.RoleInvocationContext:
        return role_runtime.RoleInvocationContext(
            repo=repo,
            role=role,
            prompt=f"run {role}",
            output_contract=role_output_contract.contract_for_role(role),
            ux_context_fingerprint=ux_context_fingerprint,
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

    def test_phoodab_72_shape_materializes_captured_text_without_model_file_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / workflow_stages.DIAGNOSTICS_FILE).write_text("{}\n", encoding="utf-8")
            runtime = self._runtime()
            context = self._context(
                repo,
                ux_context_fingerprint="ux-context-active-for-phoodab-72",
            )
            commands: list[list[str]] = []
            reader_path = current / "reader-brief.md"

            def runner(command, **kwargs):
                commands.append(list(command))
                self.assertFalse(reader_path.exists())
                return _Completed(
                    stdout="\n".join(
                        [
                            json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
                            _text_event(
                                "# Reader handoff\n\nRepository facts returned by ollama/gpt-oss:20b-autodev."
                            ),
                            json.dumps({"type": "step_finish", "part": {"type": "step-finish"}}),
                        ]
                    )
                )

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
            command = commands[0]
            self.assertEqual(command[1], "run")
            self.assertEqual(command[command.index("--model") + 1], "ollama/gpt-oss:20b-autodev")
            self.assertIn("single compatibility fallback-text attempt", command[-1])
            self.assertIn("Do not write or edit", command[-1])
            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.READER_SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(result.schema_retry_count, 2)
            self.assertTrue(reader_path.is_file())
            self.assertIn(
                "Repository facts returned by ollama/gpt-oss:20b-autodev",
                reader_path.read_text(encoding="utf-8"),
            )

            role_coordinator_runtime._record_attempt(
                repo,
                "reader",
                result,
                reader_path,
                accepted=True,
                ux_context_fingerprint=context.ux_context_fingerprint,
            )
            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(
                diagnostics["last_structured_output"]["state"],
                opencode_role_runtime.READER_SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(
                diagnostics["reader_fallback_materialization"],
                {
                    "source": "captured-cli-text",
                    "state": opencode_cli_text.FALLBACK_MATERIALIZATION_CAPTURED_TEXT,
                },
            )
            self.assertEqual(
                int((diagnostics.get("protocol_correction_attempts", {}) or {}).get("reader", 0)),
                0,
            )

            synthesis = opencode_adapter_handoff._prepare_synthesizer(
                current,
                "PHOODAB #72",
            )
            self.assertIn("Repository facts returned by ollama", synthesis)

    def test_fallback_ignores_explicitly_synthetic_text_and_materializes_visible_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            runtime = self._runtime()
            context = self._context(repo)
            stdout = "\n".join(
                [
                    _text_event("internal continuation", synthetic=True),
                    _text_event(
                        "synthetic continuation metadata",
                        metadata={"compaction_continue": True},
                    ),
                    _text_event("Visible factual Reader handoff."),
                ]
            )
            exhausted = opencode_structured_output.StructuredOutputExhausted(
                "StructuredOutputError",
                retries=1,
            )

            result, native = self._invoke_with_native(
                runtime,
                context,
                exhausted,
                lambda command, **kwargs: _Completed(stdout=stdout),
            )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(result.termination, "completed")
            text = (current / "reader-brief.md").read_text(encoding="utf-8")
            self.assertIn("Visible factual Reader handoff", text)
            self.assertNotIn("internal continuation", text)
            self.assertNotIn("synthetic continuation metadata", text)

    def test_empty_text_fallback_terminates_once_as_role_protocol_exhausted(self):
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
                return _Completed(stdout=json.dumps({"type": "step_finish", "part": {"type": "step-finish"}}))

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
            self.assertIn("produced no completed factual text event", result.stderr)
            self.assertFalse((current / "reader-brief.md").exists())

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
                diagnostics["reader_fallback_materialization"]["state"],
                opencode_cli_text.FALLBACK_MATERIALIZATION_REJECTED,
            )
            self.assertEqual(
                int((diagnostics.get("protocol_correction_attempts", {}) or {}).get("reader", 0)),
                0,
            )

    def test_malformed_non_event_fallback_text_is_rejected_deterministically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            runtime = self._runtime()
            context = self._context(repo)
            exhausted = opencode_structured_output.StructuredOutputExhausted(
                "StructuredOutputError",
                retries=2,
            )

            result, native = self._invoke_with_native(
                runtime,
                context,
                exhausted,
                lambda command, **kwargs: _Completed(
                    stdout="Repository facts without OpenCode JSON event framing."
                ),
            )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(result.termination, "structured-output-exhausted")
            self.assertIn("malformed OpenCode JSON", result.stderr)
            self.assertFalse((current / "reader-brief.md").exists())

    def test_oversized_fallback_handoff_is_rejected_not_truncated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            runtime = self._runtime()
            context = self._context(repo)
            exhausted = opencode_structured_output.StructuredOutputExhausted(
                "StructuredOutputError",
                retries=2,
            )
            oversized = "x" * (opencode_adapter_contract.MAX_HANDOFF_CHARS + 1)

            result, _native = self._invoke_with_native(
                runtime,
                context,
                exhausted,
                lambda command, **kwargs: _Completed(stdout=_text_event(oversized)),
            )

            self.assertEqual(result.termination, "structured-output-exhausted")
            self.assertIn("exceeds the", result.stderr)
            self.assertFalse((current / "reader-brief.md").exists())

    def test_nonzero_fallback_records_invocation_failure_without_second_native_sequence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            runtime = self._runtime()
            context = self._context(repo)
            commands: list[list[str]] = []
            exhausted = opencode_structured_output.StructuredOutputExhausted(
                "StructuredOutputError",
                retries=2,
            )

            result, native = self._invoke_with_native(
                runtime,
                context,
                exhausted,
                lambda command, **kwargs: (
                    commands.append(list(command))
                    or _Completed(returncode=7, stdout="", stderr="runtime failed")
                ),
            )

            self.assertEqual(native.call_count, 1)
            self.assertEqual(len(commands), 1)
            self.assertEqual(result.termination, "structured-output-exhausted")
            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(
                diagnostics["reader_fallback_materialization"]["state"],
                opencode_cli_text.FALLBACK_MATERIALIZATION_INVOCATION_FAILED,
            )

    def test_supported_non_reader_schema_exhaustion_uses_generic_fallback(self):
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
            self.assertEqual(len(commands), 1)
            self.assertEqual(result.termination, "completed")
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(result.schema_retry_count, 2)


if __name__ == "__main__":
    unittest.main()

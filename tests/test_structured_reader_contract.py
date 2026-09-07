from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    execution_classification as execution,
    execution_classification_reader_advisory,
    opencode_adapter_handoff,
    opencode_adapter_roles,
    opencode_role_runtime,
    opencode_structured_output,
    role_output_contract,
    role_runtime,
    workflow_stages,
)
from automation.opencode_adapter_contract import OpenCodeAdapterError


class _Completed:
    returncode = 0
    stdout = "{}"
    stderr = ""


class StructuredReaderContractTests(unittest.TestCase):
    def _protocol_current(self, repo: Path) -> Path:
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True, exist_ok=True)
        issue_text = "# Issue\n\nImplement repository tests for the runtime.\n"
        state: dict[str, object] = {
            "Status": "Prepared",
            "IssueNumber": 236,
            "IssueText": issue_text,
            "ProviderProfile": "",
        }
        execution.enable_protocol(state)
        workflow_stages.write_state(current, state)
        (current / "issue.md").write_text(issue_text, encoding="utf-8")
        return current

    def _write_ux_context(self, current: Path) -> None:
        (current / "ux-context-reader.json").write_text(
            json.dumps(
                {
                    "ux_context": {
                        "journeys": ["create-task"],
                        "screens": ["task-editor"],
                        "states": ["task-editor-empty"],
                        "contract": "interaction-contract.md",
                        "principles": "principles.md",
                    },
                    "ux_context_fingerprint": "reader-ux",
                }
            ),
            encoding="utf-8",
        )

    def _payload(self, source_id: str = "task-editor-empty") -> dict[str, object]:
        return {
            "handoff_markdown": "# Reader handoff\n\nThe runtime seam is in automation/.",
            "repository_evidence": [
                {
                    "path": "automation/role_runtime.py",
                    "observation": "The provider-neutral runtime seam is defined here.",
                }
            ],
            "ux": {
                "constraints_addressed": [
                    {
                        "source_kind": "state",
                        "source_id": source_id,
                        "impact": "Keep the selected empty-state requirement visible downstream.",
                    }
                ],
                "open_questions": [],
            },
        }

    def test_reader_contract_is_static_bounded_and_contains_no_execution_authority(self):
        contract = role_output_contract.contract_for_role("reader")
        self.assertIsNotNone(contract)
        assert contract is not None
        self.assertEqual(contract.identity, "autodev.reader/v1")
        schema = contract.schema()
        text = json.dumps(schema, sort_keys=True).casefold()
        self.assertEqual(schema["properties"]["handoff_markdown"]["maxLength"], 30000)
        self.assertLessEqual(schema["properties"]["repository_evidence"]["maxItems"], 128)
        self.assertNotIn("execution_classification", text)
        self.assertNotIn("manual-external", text)
        self.assertNotIn("attention_required", text)
        self.assertNotIn("ux_context_fingerprint", text)
        self.assertNotIn("create-task", text)
        self.assertNotIn("task-editor-empty", text)

    def test_native_reader_materializes_factual_artifacts_without_touching_control_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._protocol_current(repo)
            self._write_ux_context(current)
            before = workflow_stages.read_state(current)
            contract = role_output_contract.contract_for_role("reader")
            assert contract is not None

            target = role_output_contract.materialize_structured_output(
                repo, contract, self._payload()
            )

            assert target is not None
            self.assertIn("Reader handoff", target.read_text(encoding="utf-8"))
            evidence = json.loads(
                (current / "structured-reader-evidence.json").read_text(encoding="utf-8")
            )
            self.assertEqual(evidence[0]["path"], "automation/role_runtime.py")
            self.assertTrue((current / "structured-ux-reader.json").is_file())
            after = workflow_stages.read_state(current)
            self.assertEqual(
                after.get("ExecutionClassification"),
                before.get("ExecutionClassification"),
            )
            self.assertFalse((current / execution.CLASSIFICATION_FILE).exists())

    def test_reader_structured_payload_cannot_smuggle_execution_classification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._protocol_current(repo)
            contract = role_output_contract.contract_for_role("reader")
            assert contract is not None
            payload = self._payload()
            payload["execution_classification"] = "manual-external"
            before = workflow_stages.read_state(current)

            with self.assertRaises(role_output_contract.RoleOutputContractError) as raised:
                role_output_contract.materialize_structured_output(repo, contract, payload)

            self.assertIn("unsupported control-plane field", str(raised.exception))
            after = workflow_stages.read_state(current)
            self.assertEqual(after, before)

    def test_reader_selected_ux_reference_is_checked_after_factual_handoff_acceptance(self):
        original_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
        try:
            execution_classification_reader_advisory.install()
            with tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                current = self._protocol_current(repo)
                self._write_ux_context(current)
                contract = role_output_contract.contract_for_role("reader")
                assert contract is not None
                target = role_output_contract.materialize_structured_output(
                    repo, contract, self._payload()
                )
                assert target is not None

                outputs = opencode_adapter_roles._accept_role_once("reader", current, target)

                self.assertEqual(
                    {path.name for path in outputs},
                    {"reader-brief.md", "synthesized-handoff.md"},
                )
                state = workflow_stages.read_state(current)
                self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
        finally:
            opencode_adapter_roles._accept_role_once = original_accept  # type: ignore[attr-defined]

    def test_reader_invented_ux_reference_is_protocol_rejection_not_classification(self):
        original_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
        try:
            execution_classification_reader_advisory.install()
            with tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                current = self._protocol_current(repo)
                self._write_ux_context(current)
                contract = role_output_contract.contract_for_role("reader")
                assert contract is not None
                target = role_output_contract.materialize_structured_output(
                    repo, contract, self._payload("invented-state")
                )
                assert target is not None
                before = workflow_stages.read_state(current)

                with self.assertRaises(OpenCodeAdapterError) as raised:
                    opencode_adapter_roles._accept_role_once("reader", current, target)

                self.assertIn("outside the effective selected UX context", str(raised.exception))
                after = workflow_stages.read_state(current)
                self.assertEqual(
                    after.get("ExecutionClassification"),
                    before.get("ExecutionClassification"),
                )
        finally:
            opencode_adapter_roles._accept_role_once = original_accept  # type: ignore[attr-defined]

    def test_opencode_native_reader_uses_schema_path_and_falls_back_when_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = opencode_role_runtime.OpenCodeRoleRuntime()
            runtime._mappings = {
                "reader": {
                    "agent": "autodev-reader",
                    "model": "openai/test-model",
                    "source": "test",
                    "inherits_from": "",
                }
            }
            context = role_runtime.RoleInvocationContext(
                repo=repo,
                role="reader",
                prompt="read repository",
                output_contract=role_output_contract.contract_for_role("reader"),
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
                return_value=opencode_structured_output.NativeStructuredOutput(
                    {
                        "handoff_markdown": "Reader handoff",
                        "repository_evidence": [],
                        "ux": {"constraints_addressed": [], "open_questions": []},
                    }
                ),
            ) as native:
                result = runtime.invoke(context, runner=lambda *a, **k: _Completed())
            self.assertEqual(result.structured_output_mode, "native-validated")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(native.call_args.kwargs["role"], "reader")
            self.assertTrue(
                (repo / workflow_stages.CURRENT_DIR / "reader-brief.md").is_file()
            )

            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return _Completed()

            with patch.object(
                opencode_role_runtime.opencode_structured_output,
                "invoke",
                side_effect=opencode_structured_output.StructuredOutputUnavailable("old runtime"),
            ):
                result = runtime.invoke(context, runner=runner, which=lambda _name: "opencode")
            self.assertEqual(result.structured_output_mode, "fallback-text")
            self.assertEqual(result.structured_output_state, "native-unavailable")
            self.assertEqual(commands[0][1], "run")
            self.assertIn("--format", commands[0])


if __name__ == "__main__":
    unittest.main()

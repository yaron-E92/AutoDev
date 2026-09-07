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
    workflow_stages,
)


class _Completed:
    returncode = 0
    stdout = "{}"
    stderr = ""


class StructuredCompletionContractTests(unittest.TestCase):
    def test_completion_schemas_are_static_bounded_and_non_authoritative(self):
        for role in ("implementer", "fixer"):
            contract = role_output_contract.contract_for_role(role)
            self.assertIsNotNone(contract)
            assert contract is not None
            schema = contract.schema()
            text = json.dumps(schema, sort_keys=True).casefold()
            self.assertEqual(schema["additionalProperties"], False)
            self.assertLessEqual(
                schema["properties"]["completion_summary"]["maxLength"],
                8000,
            )
            self.assertLessEqual(
                schema["properties"]["validation_notes"]["maxItems"],
                32,
            )
            for forbidden in (
                "workflow_stage",
                "stage_completed",
                "verification_passed",
                "source_identity",
                "execution_classification",
                "ux_context_fingerprint",
            ):
                self.assertNotIn(forbidden, text)
        implementer = role_output_contract.contract_for_role("implementer")
        assert implementer is not None
        commit = implementer.schema()["properties"]["commit_message"]
        self.assertEqual(commit["maxLength"], 200)
        self.assertIn("pattern", commit)

    def test_implementer_native_report_materializes_existing_commit_message_artifact(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            contract = role_output_contract.contract_for_role("implementer")
            assert contract is not None
            target = role_output_contract.materialize_structured_output(
                repo,
                contract,
                {
                    "commit_message": "Add structured completion reports",
                    "completion_summary": "Implemented the requested source changes.",
                    "validation_notes": ["Unit tests requested by the prompt were run."],
                },
            )
            assert target is not None
            self.assertEqual(target.name, "commit-message.txt")
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "Add structured completion reports\n",
            )
            report = json.loads(
                (
                    repo
                    / workflow_stages.CURRENT_DIR
                    / "structured-completion-implementer.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(report["commit_message"], "Add structured completion reports")
            self.assertIn("completion_summary", report)

    def test_fixer_native_report_is_evidence_only_and_does_not_create_workflow_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            contract = role_output_contract.contract_for_role("fixer")
            assert contract is not None
            target = role_output_contract.materialize_structured_output(
                repo,
                contract,
                {
                    "completion_summary": "Applied only the targeted repair.",
                    "validation_notes": ["The next verifier still decides acceptance."],
                },
            )
            assert target is not None
            self.assertEqual(target.name, "structured-completion-fixer.json")
            self.assertTrue(target.is_file())
            current = repo / workflow_stages.CURRENT_DIR
            self.assertFalse((current / "state.json").exists())
            self.assertFalse((current / "run-manifest.json").exists())
            report = json.loads(target.read_text(encoding="utf-8"))
            self.assertNotIn("verified", report)
            self.assertNotIn("source_identity", report)

    def test_completion_snapshot_identity_includes_contract_version(self):
        for role in ("implementer", "fixer"):
            original = {
                "fingerprint": "c" * 64,
                "safe_metadata": {"runtime": "opencode", "model": "provider/model"},
            }
            bound = role_output_contract.bind_role_snapshot(original, role)
            self.assertNotEqual(bound["fingerprint"], original["fingerprint"])
            binding = bound["safe_metadata"]["role_output_binding"]
            self.assertEqual(binding["contract"]["identity"], f"autodev.{role}-result/v1")

    def test_opencode_native_completion_reports_and_fallback_paths(self):
        payloads = {
            "implementer": {
                "commit_message": "Implement structured completion",
                "completion_summary": "Source edits complete.",
                "validation_notes": [],
            },
            "fixer": {
                "completion_summary": "Targeted repair complete.",
                "validation_notes": [],
            },
        }
        for role, payload in payloads.items():
            with self.subTest(role=role), tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                runtime = opencode_role_runtime.OpenCodeRoleRuntime()
                runtime._mappings = {
                    role: {
                        "agent": f"autodev-{role}",
                        "model": "openai/test-model",
                        "source": "test",
                        "inherits_from": "",
                    }
                }
                context = role_runtime.RoleInvocationContext(
                    repo=repo,
                    role=role,
                    prompt=f"run {role}",
                    repair_kind="local" if role == "fixer" else "",
                    output_contract=role_output_contract.contract_for_role(role),
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
                    return_value=opencode_structured_output.NativeStructuredOutput(payload),
                ) as native:
                    result = runtime.invoke(
                        context,
                        runner=lambda *a, **k: _Completed(),
                    )
                self.assertEqual(result.structured_output_mode, "native-validated")
                self.assertEqual(result.returncode, 0)
                self.assertEqual(native.call_args.kwargs["role"], role)

                commands: list[list[str]] = []

                def runner(command, **kwargs):
                    commands.append(list(command))
                    return _Completed()

                with patch.object(
                    opencode_role_runtime.opencode_structured_output,
                    "invoke",
                    side_effect=opencode_structured_output.StructuredOutputUnavailable(
                        "native format unavailable"
                    ),
                ):
                    fallback = runtime.invoke(
                        context,
                        runner=runner,
                        which=lambda _name: "opencode",
                    )
                self.assertEqual(fallback.structured_output_mode, "fallback-text")
                self.assertEqual(fallback.structured_output_state, "native-unavailable")
                self.assertEqual(commands[0][1], "run")
                self.assertIn("--format", commands[0])


if __name__ == "__main__":
    unittest.main()

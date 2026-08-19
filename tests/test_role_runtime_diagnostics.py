from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    non_success_report,
    opencode_adapter,
    opencode_coordinator,
    role_runtime_diagnostics,
    workflow_stages,
)


class RoleRuntimeDiagnosticsTests(unittest.TestCase):
    def _repo(self, root: str, issue: int = 176) -> tuple[Path, Path]:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        (current / "state.json").write_text(
            json.dumps({"IssueNumber": issue, "AcceptedRoleArtifacts": {}}),
            encoding="utf-8",
        )
        (current / workflow_stages.DIAGNOSTICS_FILE).write_text(
            json.dumps({"role_invocations": {"reader": 1}}),
            encoding="utf-8",
        )
        return repo, current

    def test_artifact_states_distinguish_missing_zero_sanitized_and_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing.md"
            zero = root / "zero.md"
            sanitized = root / "sanitized.md"
            invalid = root / "invalid.md"
            zero.write_bytes(b"")
            sanitized.write_text("\x00\x01\r\n   \n", encoding="utf-8")
            invalid.write_text("content that does not satisfy the contract\n", encoding="utf-8")

            self.assertEqual(
                role_runtime_diagnostics.inspect_artifact(missing)["artifact_state"],
                "artifact-missing",
            )
            self.assertEqual(
                role_runtime_diagnostics.inspect_artifact(zero)["artifact_state"],
                "artifact-zero-byte",
            )
            self.assertEqual(
                role_runtime_diagnostics.inspect_artifact(sanitized)["artifact_state"],
                "artifact-empty-after-sanitization",
            )
            self.assertEqual(
                role_runtime_diagnostics.inspect_artifact(
                    invalid,
                    validation_error="reader output has invalid structure",
                )["artifact_state"],
                "artifact-acceptance-failed",
            )

    def test_runtime_excerpts_are_bounded_and_redact_secrets(self):
        value = "prefix Authorization: Bearer super-secret-token token=another-secret " + ("x" * 4000)
        excerpt = role_runtime_diagnostics.runtime_excerpt(value)
        self.assertLessEqual(len(excerpt), role_runtime_diagnostics.MAX_RUNTIME_EXCERPT_CHARS)
        self.assertNotIn("super-secret-token", excerpt)
        self.assertNotIn("another-secret", excerpt)

    def test_two_zero_exit_empty_reader_attempts_are_durable_and_role_protocol_exhausted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            output = current / "reader-brief.md"
            output.write_bytes(b"")
            (current / "contract-correction-reader.md").write_text("correct the reader output\n", encoding="utf-8")
            completed = SimpleNamespace(
                returncode=0,
                stdout='{"type":"text","text":"claimed success token=runtime-secret"}\n',
                stderr="",
            )
            rejection = opencode_adapter.OpenCodeAdapterError(
                "reader protocol correction limit exhausted after one retry: role result is empty: "
                ".autodev-run/current/reader-brief.md"
            )

            with patch.object(opencode_adapter, "prepare_role"), patch.object(
                opencode_adapter,
                "accept_role",
                side_effect=[
                    opencode_adapter.OpenCodeAdapterError(
                        "role result is empty: .autodev-run/current/reader-brief.md"
                    ),
                    rejection,
                ],
            ):
                with self.assertRaises(opencode_coordinator.OpenCodeCoordinatorError) as raised:
                    opencode_coordinator.run_role(
                        repo,
                        "reader",
                        runner=lambda *args, **kwargs: completed,
                        which=lambda _: "/usr/bin/opencode",
                    )

            self.assertEqual(
                raised.exception.classification,
                role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
            )
            self.assertIn("diagnostic:", str(raised.exception))
            diagnostic = repo / raised.exception.diagnostic_path
            self.assertTrue(diagnostic.is_file())

            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(diagnostics["role_invocations"], {"reader": 1})
            self.assertEqual(diagnostics["role_physical_attempts"], {"reader": 2})
            self.assertEqual(diagnostics["protocol_correction_attempts"], {"reader": 1})

            attempts = sorted((current / role_runtime_diagnostics.ROLE_ATTEMPT_DIR).glob("reader-*.json"))
            self.assertEqual(len(attempts), 2)
            initial = json.loads(attempts[0].read_text(encoding="utf-8"))
            correction = json.loads(attempts[1].read_text(encoding="utf-8"))
            self.assertEqual(initial["attempt_kind"], "initial")
            self.assertEqual(initial["returncode"], 0)
            self.assertEqual(initial["artifact_state"], "artifact-zero-byte")
            self.assertEqual(correction["attempt_kind"], "protocol-correction")
            self.assertEqual(correction["returncode"], 0)
            self.assertEqual(correction["artifact_state"], "artifact-zero-byte")
            self.assertNotIn("runtime-secret", correction["stdout_excerpt"])

            last_failure = json.loads(
                (current / role_runtime_diagnostics.LAST_FAILURE_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(last_failure["physical_role_attempt"], 2)
            self.assertEqual(last_failure["protocol_correction_attempts"], 1)
            self.assertEqual(
                last_failure["failure_classification"],
                role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
            )
            self.assertEqual(last_failure["diagnostic_path"], raised.exception.diagnostic_path)

    def test_successful_protocol_correction_records_two_attempts_and_clears_last_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            output = current / "reader-brief.md"
            output.write_bytes(b"")
            (current / "contract-correction-reader.md").write_text("correct the reader output\n", encoding="utf-8")
            completed = SimpleNamespace(returncode=0, stdout='{"type":"text"}\n', stderr="")
            calls = 0

            def runner(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    output.write_text("# Reader brief\n\nUseful repository evidence.\n", encoding="utf-8")
                return completed

            accepted = {
                "state": "ACCEPTED",
                "role": "reader",
                "artifact": ".autodev-run/current/reader-brief.md",
                "sha256": "abc",
            }
            with patch.object(opencode_adapter, "prepare_role"), patch.object(
                opencode_adapter,
                "accept_role",
                side_effect=[
                    opencode_adapter.OpenCodeAdapterError(
                        "role result is empty: .autodev-run/current/reader-brief.md"
                    ),
                    [],
                ],
            ), patch.object(
                opencode_coordinator,
                "role_acceptance",
                return_value=accepted,
            ):
                result = opencode_coordinator.run_role(
                    repo,
                    "reader",
                    runner=runner,
                    which=lambda _: "/usr/bin/opencode",
                )

            self.assertEqual(result["state"], "ACCEPTED")
            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )
            self.assertEqual(diagnostics["role_physical_attempts"], {"reader": 2})
            self.assertEqual(diagnostics["protocol_correction_attempts"], {"reader": 1})
            self.assertFalse((current / role_runtime_diagnostics.LAST_FAILURE_FILE).exists())
            attempts = sorted((current / role_runtime_diagnostics.ROLE_ATTEMPT_DIR).glob("reader-*.json"))
            self.assertEqual(len(attempts), 2)
            correction = json.loads(attempts[1].read_text(encoding="utf-8"))
            self.assertTrue(correction["accepted"])
            self.assertEqual(correction["artifact_state"], "accepted")

    def test_nonzero_runtime_exit_is_transient_and_persists_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            completed = SimpleNamespace(
                returncode=17,
                stdout="",
                stderr="provider unavailable token=secret-value",
            )
            with patch.object(opencode_adapter, "prepare_role"):
                with self.assertRaises(opencode_coordinator.OpenCodeCoordinatorError) as raised:
                    opencode_coordinator.run_role(
                        repo,
                        "reader",
                        runner=lambda *args, **kwargs: completed,
                        which=lambda _: "/usr/bin/opencode",
                    )

            self.assertEqual(raised.exception.classification, workflow_stages.FAILURE_TRANSIENT)
            diagnostic = repo / raised.exception.diagnostic_path
            record = json.loads(diagnostic.read_text(encoding="utf-8"))
            self.assertEqual(record["termination"], "runtime-nonzero")
            self.assertEqual(record["returncode"], 17)
            self.assertNotIn("secret-value", record["stderr_excerpt"])

    def test_non_success_report_surfaces_decisive_role_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(temp_dir)
            output = current / "reader-brief.md"
            output.write_bytes(b"")
            first = role_runtime_diagnostics.record_attempt(
                repo,
                role="reader",
                phase="work",
                runtime="opencode",
                output_path=output,
                returncode=0,
                elapsed_ms=10,
                stdout="claimed success",
                accepted=False,
                validation_error="role result is empty",
                failure_classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL,
                failure_reason="reader output rejected",
            )
            second = role_runtime_diagnostics.record_attempt(
                repo,
                role="reader",
                phase="correction",
                runtime="opencode",
                output_path=output,
                returncode=0,
                elapsed_ms=11,
                stdout="claimed correction success",
                accepted=False,
                validation_error="role result is empty",
                failure_classification=role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                failure_reason="reader protocol correction exhausted",
            )
            self.assertNotEqual(first, second)

            payload, _ = non_success_report.update_report(
                repo,
                {
                    "state": "FAILED",
                    "issue_number": 176,
                    "failed_stage": "python-coordinator",
                    "failure_classification": role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
                    "reason": f"reader protocol correction exhausted; diagnostic: {second}",
                },
            )
            self.assertEqual(payload["state"], "FAILED")
            report = (current / non_success_report.REPORT_NAME).read_text(encoding="utf-8")
            self.assertIn("role-protocol-exhausted", report)
            self.assertIn(second, report)
            self.assertIn('"physical_role_attempt": 2', report)
            self.assertIn('"protocol_correction_attempts": 1', report)


if __name__ == "__main__":
    unittest.main()

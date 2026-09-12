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
    role_coordinator_runtime,
    role_output_contract,
    role_runtime,
)


def _events(text: str) -> str:
    return json.dumps(
        {
            "type": "text",
            "part": {
                "type": "text",
                "text": text,
            },
        }
    ) + "\n"


class SchemaExhaustionFallbackGeneralizationTests(unittest.TestCase):
    def _runtime(self) -> opencode_role_runtime.OpenCodeRoleRuntime:
        runtime = opencode_role_runtime.OpenCodeRoleRuntime()
        runtime._mappings = {
            role: {
                "agent": f"autodev-{role}",
                "model": "ollama/gpt-oss:20b-autodev",
                "source": "test",
                "inherits_from": "",
            }
            for role in ("reader", "synthesizer", "planner", "verifier", "implementer")
        }
        return runtime

    def _context(self, repo: Path, role: str, *, phase: str = "work"):
        return role_runtime.RoleInvocationContext(
            repo=repo,
            role=role,
            prompt=f"run {role}",
            phase=phase,
            output_contract=role_output_contract.contract_for_role(role),
            ux_context_fingerprint="ux-identity",
        )

    def _runtime_patches(self, side_effect):
        return (
            patch.object(
                opencode_role_runtime.opencode_cli,
                "resolve_opencode_cli",
                return_value="opencode",
            ),
            patch.object(
                opencode_role_runtime.privacy,
                "load_policy",
                return_value=SimpleNamespace(enabled=False),
            ),
            patch.object(
                opencode_role_runtime.opencode_structured_output,
                "invoke",
                side_effect=side_effect,
            ),
        )

    def test_shuffle_task_308_reader_then_synthesizer_both_recover(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run/current"
            current.mkdir(parents=True)
            (current / "run-diagnostics.json").write_text("{}\n", encoding="utf-8")
            responses = iter(
                [
                    _events("Reader factual handoff for ShuffleTask #308."),
                    _events("Synthesized handoff for ShuffleTask #308."),
                ]
            )
            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return SimpleNamespace(returncode=0, stdout=next(responses), stderr="")

            runtime = self._runtime()
            cli_patch, privacy_patch, native_patch = self._runtime_patches(
                [
                    opencode_structured_output.StructuredOutputExhausted(
                        "reader schema exhausted", retries=2
                    ),
                    opencode_structured_output.StructuredOutputExhausted(
                        "synthesizer schema exhausted", retries=2
                    ),
                ]
            )
            with cli_patch, privacy_patch, native_patch as native:
                reader = runtime.invoke(self._context(repo, "reader"), runner=runner)
                synthesizer = runtime.invoke(
                    self._context(repo, "synthesizer"), runner=runner
                )

            self.assertEqual(native.call_count, 2)
            self.assertEqual(len(commands), 2)
            for result in (reader, synthesizer):
                self.assertEqual(result.termination, "completed")
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.structured_output_mode, "fallback-text")
                self.assertEqual(
                    result.structured_output_state,
                    opencode_role_runtime.SCHEMA_FALLBACK_STATE,
                )
                self.assertEqual(result.schema_retry_count, 2)
                self.assertEqual(result.model, "ollama/gpt-oss:20b-autodev")

            self.assertEqual(
                (current / "reader-brief.md").read_text(encoding="utf-8"),
                "Reader factual handoff for ShuffleTask #308.\n",
            )
            self.assertEqual(
                (current / "synthesized-handoff.md").read_text(encoding="utf-8"),
                "Synthesized handoff for ShuffleTask #308.\n",
            )
            self.assertIn("schema-exhaustion fallback", commands[1][-1])
            self.assertIn("same synthesizer role, model/provider route, phase", commands[1][-1])
            self.assertEqual(
                commands[1][commands[1].index("--model") + 1],
                "ollama/gpt-oss:20b-autodev",
            )

            diagnostics = json.loads(
                (current / "run-diagnostics.json").read_text(encoding="utf-8")
            )
            synth_materialization = diagnostics["fallback_materialization"][
                "synthesizer:work"
            ]
            self.assertEqual(
                synth_materialization["source"], "captured-runtime-output"
            )
            self.assertEqual(synth_materialization["state"], "succeeded")

    def test_planner_schema_exhaustion_materializes_existing_text_protocol(self):
        plan = """1) Where to look

automation/opencode_role_runtime.py

2) Files / areas likely to touch

automation/opencode_role_runtime.py and tests

3) Assumptions

The #307 fallback parser remains authoritative.

4) Plan

Generalize schema exhaustion dispatch through the fallback contract.

5) Risks / gotchas

Never restart native Structured Output after exhaustion.

6) Recommended implementation approach

Use the existing bounded fallback-text materialization path exactly once.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            runtime = self._runtime()
            commands: list[list[str]] = []

            def runner(command, **kwargs):
                commands.append(list(command))
                return SimpleNamespace(returncode=0, stdout=_events(plan), stderr="")

            cli_patch, privacy_patch, native_patch = self._runtime_patches(
                opencode_structured_output.StructuredOutputExhausted(
                    "planner schema exhausted", retries=2
                )
            )
            with cli_patch, privacy_patch, native_patch as native:
                result = runtime.invoke(self._context(repo, "planner"), runner=runner)

            self.assertEqual(native.call_count, 1)
            self.assertEqual(len(commands), 1)
            self.assertEqual(result.termination, "completed")
            self.assertEqual(
                result.structured_output_state,
                opencode_role_runtime.SCHEMA_FALLBACK_STATE,
            )
            self.assertEqual(result.schema_retry_count, 2)
            artifact = repo / ".autodev-run/current/plan.md"
            self.assertTrue(artifact.is_file())
            self.assertIn(
                "Generalize schema exhaustion dispatch",
                artifact.read_text(encoding="utf-8"),
            )

    def test_rejected_schema_fallback_correction_stays_fallback_only(self):
        valid_plan = """1) Where to look

automation/

2) Files / areas likely to touch

automation/opencode_role_runtime.py

3) Assumptions

None.

4) Plan

Correct the fallback result.

5) Risks / gotchas

Do not restart native mode.

6) Recommended implementation approach

Return the valid text protocol.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run/current"
            current.mkdir(parents=True)
            (current / "run-diagnostics.json").write_text(
                json.dumps({"role_invocations": {"planner": 1}}),
                encoding="utf-8",
            )
            runtime = self._runtime()
            cli_patch, privacy_patch, native_patch = self._runtime_patches(
                opencode_structured_output.StructuredOutputExhausted(
                    "planner schema exhausted", retries=2
                )
            )
            with cli_patch, privacy_patch, native_patch:
                first = runtime.invoke(
                    self._context(repo, "planner"),
                    runner=lambda *a, **k: SimpleNamespace(
                        returncode=0,
                        stdout=_events("not a valid AutoDev plan"),
                        stderr="",
                    ),
                )

            self.assertEqual(first.termination, "completed")
            self.assertFalse((current / "plan.md").exists())
            role_coordinator_runtime._record_attempt(
                repo,
                "planner",
                first,
                current / "plan.md",
                accepted=False,
                validation_error="plan missing after parser rejection",
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
            ) as native:
                corrected = runtime.invoke(
                    self._context(repo, "planner", phase="correction"),
                    runner=lambda *a, **k: SimpleNamespace(
                        returncode=0,
                        stdout=_events(valid_plan),
                        stderr="",
                    ),
                )

            native.assert_not_called()
            self.assertEqual(corrected.termination, "completed")
            self.assertEqual(
                corrected.structured_output_state,
                opencode_role_runtime.FALLBACK_CORRECTION_STATE,
            )
            self.assertTrue((current / "plan.md").is_file())


if __name__ == "__main__":
    unittest.main()

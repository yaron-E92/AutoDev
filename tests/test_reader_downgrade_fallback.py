from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    execution_classification as execution,
    execution_classification_boundary as boundary,
    execution_classification_hooks,
    opencode_adapter_contract,
    opencode_adapter_handoff,
    opencode_adapter_roles,
    role_coordinator_runtime,
    role_runtime,
    workflow_stages,
)


class _ReaderRuntime:
    name = "opencode"

    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = dict(outputs)
        self.calls: list[str] = []

    def invoke(self, context, *, runner, which=None):
        self.calls.append(context.phase)
        output = context.repo / workflow_stages.CURRENT_DIR / "reader-brief.md"
        output.write_text(self.outputs.get(context.phase, ""), encoding="utf-8")
        return role_runtime.RoleInvocationResult(
            runtime=self.name,
            role=context.role,
            phase=context.phase,
            returncode=0,
            elapsed_ms=1,
            stdout='{"type":"text","text":"reader completed"}\n',
            stderr="",
            termination="completed",
            model="test/reader",
        )


class ReaderClassificationAdvisoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_prepare = opencode_adapter_handoff._prepare_reader  # type: ignore[attr-defined]
        self._original_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
        self._original_correction = opencode_adapter_roles._reader_correction_contract  # type: ignore[attr-defined]
        execution_classification_hooks._install_reader_gate()
        boundary.install()

    def tearDown(self) -> None:
        opencode_adapter_handoff._prepare_reader = self._original_prepare  # type: ignore[attr-defined]
        opencode_adapter_roles._accept_role_once = self._original_accept  # type: ignore[attr-defined]
        opencode_adapter_roles._reader_correction_contract = self._original_correction  # type: ignore[attr-defined]

    def _block(self, payload: dict[str, object]) -> str:
        return (
            "Useful factual repository findings.\n\n"
            + execution.CLASSIFICATION_BLOCK_START
            + "\n"
            + json.dumps(payload)
            + "\n"
            + execution.CLASSIFICATION_BLOCK_END
            + "\n"
        )

    def _automatable_invalid_payload(self) -> dict[str, object]:
        return {
            "classification": "automatable",
            "reason": "Reader says automatable but serializes contradictory manual fields.",
            "autonomous_criteria": ["Implement repository APIs and tests."],
            "manual_criteria": ["Implement repository APIs."],
            "human_actions": ["Write the repository code."],
            "resume_evidence": ["Record repository completion."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
            "external_boundaries": [],
        }

    def _manual_payload(self) -> dict[str, object]:
        return {
            "classification": "manual-external",
            "reason": "Reader speculates that a provider action is required.",
            "autonomous_criteria": [],
            "manual_criteria": ["Complete publisher identity validation."],
            "human_actions": ["Complete legal identity approval with the provider."],
            "resume_evidence": ["Record the non-secret certificate identifier."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
            "external_boundaries": [
                {
                    "criterion": "Complete publisher identity validation.",
                    "boundary_kind": "human-legal-provider-approval",
                    "human_action": "Complete legal identity approval with the provider.",
                    "external_system": "certificate provider",
                    "unavailable_state": "publisher identity is not approved",
                    "why_unsupported": "provider approval requires an authorized human workflow",
                }
            ],
        }

    def _setup_repo(
        self,
        root: str,
        *,
        issue_text: str,
        protocol_version: int = execution.PROTOCOL_VERSION,
        preclassified: bool = True,
    ) -> tuple[Path, Path]:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state: dict[str, object] = {
            "Status": "Prepared",
            "IssueNumber": 230,
            "IssueText": issue_text,
            "RepoFullName": "yaron-E92/AutoDev",
            "BranchName": "feat/230",
            "AcceptedRoleArtifacts": {},
            execution.PROTOCOL_STATE_FIELD: protocol_version,
        }
        if preclassified:
            execution.apply_state_fields(
                state,
                execution.classify_issue_text(issue_text),
            )
        else:
            state["ExecutionClassification"] = "pending-reader"
            state["ExecutionClassificationSource"] = "reader-required"
        workflow_stages.write_state(current, state)
        (current / "issue.md").write_text(issue_text, encoding="utf-8")
        (current / workflow_stages.DIAGNOSTICS_FILE).write_text(
            json.dumps(
                {
                    "role_invocations": {},
                    "protocol_correction_attempts": {},
                    "protocol_correction_used": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return repo, current

    def _accept_reader(
        self,
        repo: Path,
        current: Path,
        text: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        result = current / "reader-brief.md"
        result.write_text(text, encoding="utf-8")
        outputs = opencode_adapter_roles.accept_role("reader", repo, result)
        self.assertEqual(
            {path.name for path in outputs},
            {"reader-brief.md", "synthesized-handoff.md"},
        )
        state = workflow_stages.read_state(current)
        diagnostics = json.loads(
            (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
        )
        self.assertFalse((current / "contract-correction-reader.md").exists())
        return state, diagnostics

    def test_explicit_automatable_missing_reader_classification_continues(self):
        issue = """
# Implement repository feature
<!-- autodev:execution=automatable -->

Implement the API and tests.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, issue_text=issue)
            state, diagnostics = self._accept_reader(
                repo,
                current,
                "Useful factual repository findings with no classification block.\n",
            )

        self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
        self.assertEqual(state["ExecutionClassificationSource"], "operator-metadata")
        self.assertFalse(
            diagnostics["reader_execution_advisory"]["classification_block_present"]
        )

    def test_explicit_automatable_malformed_reader_classification_continues(self):
        issue = """
# Fix auth return path validation
<!-- autodev:execution=automatable -->
"""
        malformed = (
            "Useful factual repository findings.\n\n"
            + execution.CLASSIFICATION_BLOCK_START
            + "\n{ definitely-not-json }\n"
            + execution.CLASSIFICATION_BLOCK_END
            + "\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, issue_text=issue)
            state, diagnostics = self._accept_reader(repo, current, malformed)

        self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
        advisory = diagnostics["reader_execution_advisory"]
        self.assertFalse(advisory["accepted"])
        self.assertIn("invalid JSON", advisory["diagnostic"])

    def test_explicit_automatable_reader_manual_claim_cannot_override_operator(self):
        issue = """
# Add repository signing tests
<!-- autodev:execution=automatable -->
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, issue_text=issue)
            state, diagnostics = self._accept_reader(
                repo,
                current,
                self._block(self._manual_payload()),
            )

        self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
        self.assertNotEqual(state.get("QueueState"), "attention")
        self.assertEqual(
            diagnostics["reader_execution_advisory"]["reader_classification"],
            execution.MANUAL_EXTERNAL,
        )

    def test_real_reader_schema_failure_shapes_are_diagnostics_only(self):
        fixtures: dict[str, str] = {
            "automatable-plus-manual-fields": self._block(
                self._automatable_invalid_payload()
            ),
            "manual-external-missing-arrays": self._block(
                {
                    "classification": "manual-external",
                    "reason": "Missing required arrays.",
                    "autonomous_criteria": [],
                    "manual_criteria": [],
                    "human_actions": [],
                    "resume_evidence": [],
                    "manual_prerequisite_blocks_implementation": True,
                    "autonomous_subset_independent": False,
                }
            ),
            "manual-criteria-wrong-type": self._block(
                {
                    **self._manual_payload(),
                    "manual_criteria": "not-an-array",
                }
            ),
            "invalid-external-boundary-mapping": self._block(
                {
                    **self._manual_payload(),
                    "external_boundaries": [
                        {
                            "criterion": "different criterion",
                            "boundary_kind": "human-legal-provider-approval",
                            "human_action": "different action",
                            "external_system": "provider",
                            "unavailable_state": "approval absent",
                            "why_unsupported": "human approval required",
                        }
                    ],
                }
            ),
        }
        issue = "Implement persistent workspace CRUD and add EF migrations/tests."

        for name, reader_text in fixtures.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                repo, current = self._setup_repo(temp_dir, issue_text=issue)
                state, diagnostics = self._accept_reader(repo, current, reader_text)

                self.assertEqual(
                    state["ExecutionClassification"],
                    execution.AUTOMATABLE,
                )
                self.assertNotEqual(state.get("QueueState"), "attention")
                self.assertIn("reader_execution_advisory", diagnostics)

    def test_ambiguous_issue_stays_probe_when_reader_serialization_is_bad(self):
        issue = "Investigate the release situation and identify the next useful work."
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, issue_text=issue)
            state, diagnostics = self._accept_reader(
                repo,
                current,
                self._block(self._automatable_invalid_payload()),
            )

        self.assertEqual(state["ExecutionClassification"], execution.PROBE)
        self.assertFalse(
            diagnostics["reader_execution_advisory"]["accepted"]
        )
        self.assertNotEqual(state.get("QueueState"), "attention")

    def test_v1_durable_run_migrates_without_discarding_reader_handoff(self):
        issue = "Implement persistent workspace CRUD and add EF migrations/tests."
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(
                temp_dir,
                issue_text=issue,
                protocol_version=1,
                preclassified=False,
            )
            state, diagnostics = self._accept_reader(
                repo,
                current,
                "Useful durable Reader handoff without legacy classification JSON.\n",
            )
            reader_text = (current / "reader-brief.md").read_text(encoding="utf-8")

        self.assertEqual(
            state[execution.PROTOCOL_STATE_FIELD],
            execution.PROTOCOL_VERSION,
        )
        self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
        self.assertEqual(
            state["ExecutionClassificationSource"],
            "issue-text-heuristic",
        )
        self.assertIn("Useful durable Reader handoff", reader_text)
        self.assertFalse(
            diagnostics["reader_execution_advisory"]["classification_block_present"]
        )

    def test_invalid_reader_classification_does_not_invoke_correction_phase(self):
        issue = """
# Fix Next.js auth return path validation
<!-- autodev:execution=automatable -->
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, issue_text=issue)
            runtime = _ReaderRuntime(
                {
                    "work": self._block(self._automatable_invalid_payload()),
                    # If the old control-plane coupling regresses, this empty
                    # correction reproduces the observed cross-repo failure.
                    "correction": "",
                }
            )
            snapshots = {
                role: role_runtime.build_role_snapshot(
                    runtime="opencode",
                    role=role,
                    configured={"model": f"test/{role}"},
                )
                for role in opencode_adapter_contract.ROLE_NAMES
            }
            with patch.object(role_coordinator_runtime, "_prepare_role"):
                result = role_coordinator_runtime.run_role(
                    repo,
                    "reader",
                    runtime,
                    snapshots,
                    runner=lambda *_args, **_kwargs: SimpleNamespace(
                        returncode=0,
                        stdout="",
                        stderr="",
                    ),
                    which=lambda _name: "/usr/bin/opencode",
                )
            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(encoding="utf-8")
            )

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(runtime.calls, ["work"])
        self.assertEqual(
            diagnostics.get("protocol_correction_attempts", {}).get("reader", 0),
            0,
        )


if __name__ == "__main__":
    unittest.main()

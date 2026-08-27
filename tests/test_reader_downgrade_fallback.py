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
    opencode_resume_checkpoint,
    opencode_resume_execution,
    opencode_resume_manifest,
    role_coordinator_contract,
    role_coordinator_runtime,
    role_runtime,
    role_runtime_diagnostics,
    workflow_stages,
)


def mappings() -> dict[str, dict[str, str]]:
    return {
        role: {
            "agent": f"autodev-{role}",
            "source": "explicit",
            "model": f"provider/{role}",
            "inherits_from": "",
        }
        for role in opencode_adapter_contract.OPENCODE_ROLE_NAMES
    }


class _ReaderRuntime:
    name = "opencode"

    def __init__(self, outputs: dict[str, str]) -> None:
        self.outputs = dict(outputs)
        self.calls: list[str] = []

    def invoke(self, context, *, runner, which=None):
        self.calls.append(context.phase)
        output = (
            context.repo
            / workflow_stages.CURRENT_DIR
            / "reader-brief.md"
        )
        output.write_text(self.outputs[context.phase], encoding="utf-8")
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


class ReaderDowngradeFallbackTests(unittest.TestCase):
    def _block(self, payload: dict[str, object]) -> str:
        return (
            "Reader findings for the requested repository work.\n\n"
            + execution.CLASSIFICATION_BLOCK_START
            + "\n"
            + json.dumps(payload)
            + "\n"
            + execution.CLASSIFICATION_BLOCK_END
            + "\n"
        )

    def _repo_payload(
        self,
        *,
        boundaries: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "classification": "manual-external",
            "reason": "Reader incorrectly treats repository implementation as manual.",
            "autonomous_criteria": [],
            "manual_criteria": [
                "Implement .NET group controllers, API endpoints, and EF Core migrations."
            ],
            "human_actions": [
                "Write group API code and tests.",
                "Run EF Core migrations locally.",
                "Update TypeScript types and frontend API integration.",
            ],
            "resume_evidence": ["Record non-secret completion metadata."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
        }
        if boundaries is not None:
            payload["external_boundaries"] = boundaries
        return payload

    def _mismatched_repo_payload(self) -> dict[str, object]:
        payload = self._repo_payload()
        payload["external_boundaries"] = [
            {
                "criterion": "Implement .NET group controllers, API endpoints, and EF Core migrations.",
                "boundary_kind": "unsupported-external-capability",
                "human_action": "Write group API code and tests.",
                "external_system": "developer workstation",
                "unavailable_state": "repository implementation is incomplete",
                "why_unsupported": "Reader incorrectly claims a human must implement it.",
            }
        ]
        return payload

    def _mismatched_mapping_payload(self) -> dict[str, object]:
        first_criterion = "Complete the remaining group-management integration acceptance."
        second_criterion = "Complete the remaining persistence acceptance."
        first_action = "Finish the group-management integration acceptance."
        second_action = "Finish the persistence acceptance."
        return {
            "classification": "manual-external",
            "reason": "Reader still claims manual work but cannot provide a consistent boundary mapping.",
            "autonomous_criteria": [],
            "manual_criteria": [first_criterion, second_criterion],
            "human_actions": [first_action, second_action],
            "resume_evidence": ["Record non-secret completion metadata."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
            "external_boundaries": [
                {
                    "criterion": first_criterion,
                    "boundary_kind": "unsupported-external-capability",
                    "human_action": first_action,
                    "external_system": "developer workstation",
                    "unavailable_state": "the claimed acceptance state is incomplete",
                    "why_unsupported": "Reader claims a human must complete it.",
                }
            ],
        }

    def _invalid_automatable_payload(
        self,
        *,
        manual_criteria: bool = False,
        human_actions: bool = False,
        resume_evidence: bool = False,
        blocks: bool = False,
        independent: bool = False,
    ) -> dict[str, object]:
        return {
            "classification": "automatable",
            "reason": "Reader says automatable but leaves contradictory manual state.",
            "autonomous_criteria": [
                "Implement repository APIs, migrations, permission logic, and tests."
            ],
            "manual_criteria": (
                ["Implement the remaining repository API work."]
                if manual_criteria
                else []
            ),
            "human_actions": (
                ["Write the remaining repository code."]
                if human_actions
                else []
            ),
            "resume_evidence": (
                ["Record completion of the repository implementation."]
                if resume_evidence
                else []
            ),
            "manual_prerequisite_blocks_implementation": blocks,
            "autonomous_subset_independent": independent,
            "external_boundaries": [],
        }

    def _genuine_payload(self, *, mismatch: bool) -> dict[str, object]:
        criterion = "Complete publisher identity validation and certificate issuance."
        action = "Complete legal identity approval with the certificate provider."
        boundary_action = (
            "Ask another operator to approve the certificate."
            if mismatch
            else action
        )
        return {
            "classification": "manual-external",
            "reason": "A production certificate requires external provider approval.",
            "autonomous_criteria": [],
            "manual_criteria": [criterion],
            "human_actions": [action],
            "resume_evidence": ["Record the non-secret certificate identifier."],
            "manual_prerequisite_blocks_implementation": True,
            "autonomous_subset_independent": False,
            "external_boundaries": [
                {
                    "criterion": criterion,
                    "boundary_kind": "human-legal-provider-approval",
                    "human_action": boundary_action,
                    "external_system": "public code-signing certificate authority",
                    "unavailable_state": "publisher identity approval and certificate issuance are incomplete",
                    "why_unsupported": "provider legal identity approval cannot be performed by repository tooling",
                }
            ],
        }

    def _setup_repo(
        self,
        root: str,
        *,
        explicit_automatable: bool,
    ) -> tuple[Path, Path]:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        marker = (
            "\n<!-- autodev:execution=automatable -->\n"
            if explicit_automatable
            else "\n"
        )
        issue_text = (
            "# Goldilocks #6\n\n"
            "Implement repository APIs, persistence migrations, permission logic, "
            "frontend integration, and tests."
            + marker
        )
        state: dict[str, object] = {
            "Status": "Prepared",
            "IssueNumber": 6,
            "IssueText": issue_text,
            "RepoFullName": "Tax-Technology/goldilocks",
            "BranchName": "autodev/issue-6",
            "BaseSha": "base-sha",
            "BaseTreeSha": "base-tree",
            "PreparedSnapshotHash": "snapshot",
            "AcceptedRoleArtifacts": {},
        }
        execution.enable_protocol(state)
        workflow_stages.write_state(current, state)
        (current / "issue.md").write_text(issue_text, encoding="utf-8")
        (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
        (current / workflow_stages.DIAGNOSTICS_FILE).write_text(
            json.dumps({"role_invocations": {"reader": 1}}),
            encoding="utf-8",
        )
        return repo, current

    def _snapshots(self) -> dict[str, object]:
        return {
            role: role_runtime.build_role_snapshot(
                runtime="opencode",
                role=role,
                configured={"model": f"test/{role}"},
            )
            for role in opencode_adapter_contract.ROLE_NAMES
        }

    def _run_reader(
        self,
        repo: Path,
        work: str,
        correction: str,
    ) -> tuple[dict[str, object], _ReaderRuntime]:
        runtime = _ReaderRuntime({"work": work, "correction": correction})
        with patch.object(role_coordinator_runtime, "_prepare_role"):
            result = role_coordinator_runtime.run_role(
                repo,
                "reader",
                runtime,
                self._snapshots(),
                runner=lambda *_args, **_kwargs: SimpleNamespace(
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
                which=lambda _name: "/usr/bin/opencode",
            )
        return result, runtime

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

    def test_exact_goldilocks_shape_falls_back_to_explicit_automatable_after_one_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, runtime = self._run_reader(
                repo,
                self._block(self._repo_payload()),
                self._block(self._mismatched_mapping_payload()),
            )

            state = workflow_stages.read_state(current)
            fallback = json.loads(
                (current / boundary.FALLBACK_FILE).read_text(encoding="utf-8")
            )
            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(
                    encoding="utf-8"
                )
            )
            attempts = sorted(
                (current / role_runtime_diagnostics.ROLE_ATTEMPT_DIR).glob(
                    "reader-*.json"
                )
            )
            manual_plan_exists = (current / execution.MANUAL_ACTION_PLAN_FILE).exists()
            boundary_file_exists = (current / boundary.EXTERNAL_BOUNDARY_FILE).exists()
            last_failure_exists = (
                current / role_runtime_diagnostics.LAST_FAILURE_FILE
            ).exists()

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(
            state["ExecutionClassification"],
            execution.AUTOMATABLE,
        )
        self.assertEqual(
            state["ExecutionClassificationSource"],
            boundary.OPERATOR_FALLBACK_SOURCE,
        )
        self.assertTrue(state["ExecutionClassificationFallback"])
        self.assertNotEqual(state.get("QueueState"), "attention")
        self.assertFalse(manual_plan_exists)
        self.assertFalse(boundary_file_exists)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(
            diagnostics["protocol_correction_attempts"]["reader"],
            1,
        )
        self.assertEqual(
            diagnostics["role_physical_attempts"]["reader"],
            2,
        )
        self.assertEqual(
            fallback["source"],
            boundary.OPERATOR_FALLBACK_SOURCE,
        )
        self.assertIn("external_boundaries", fallback["first_rejection"])
        self.assertIn(
            "account exactly",
            fallback["correction_rejection"],
        )
        self.assertTrue(fallback["first_attempt"])
        self.assertTrue(fallback["correction_attempt"])
        self.assertFalse(last_failure_exists)

    def test_explicit_automatable_core_contract_rejections_fall_back_after_one_retry(self):
        cases = {
            "manual_criteria": {"manual_criteria": True},
            "human_actions": {"human_actions": True},
            "resume_evidence": {"resume_evidence": True},
            "blocking_flag": {"blocks": True},
            "independent_flag": {"independent": True},
        }
        for name, kwargs in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                repo, current = self._setup_repo(
                    temp_dir,
                    explicit_automatable=True,
                )
                result, runtime = self._run_reader(
                    repo,
                    self._block(self._invalid_automatable_payload(**kwargs)),
                    self._block(self._invalid_automatable_payload(**kwargs)),
                )
                state = workflow_stages.read_state(current)
                fallback = json.loads(
                    (current / boundary.FALLBACK_FILE).read_text(
                        encoding="utf-8"
                    )
                )
                diagnostics = json.loads(
                    (current / workflow_stages.DIAGNOSTICS_FILE).read_text(
                        encoding="utf-8"
                    )
                )
                attempts = sorted(
                    (current / role_runtime_diagnostics.ROLE_ATTEMPT_DIR).glob(
                        "reader-*.json"
                    )
                )
                manual_plan_exists = (
                    current / execution.MANUAL_ACTION_PLAN_FILE
                ).exists()
                boundary_file_exists = (
                    current / boundary.EXTERNAL_BOUNDARY_FILE
                ).exists()

            self.assertEqual(result["state"], "ACCEPTED")
            self.assertEqual(runtime.calls, ["work", "correction"])
            self.assertEqual(len(attempts), 2)
            self.assertEqual(
                diagnostics["protocol_correction_attempts"]["reader"],
                1,
            )
            self.assertEqual(
                diagnostics["role_physical_attempts"]["reader"],
                2,
            )
            self.assertEqual(
                state["ExecutionClassification"],
                execution.AUTOMATABLE,
            )
            self.assertEqual(
                state["ExecutionClassificationSource"],
                boundary.OPERATOR_CLASSIFICATION_FALLBACK_SOURCE,
            )
            self.assertIn(
                "automatable classification cannot contain unresolved manual",
                fallback["first_rejection"],
            )
            self.assertIn(
                "automatable classification cannot contain unresolved manual",
                fallback["correction_rejection"],
            )
            self.assertFalse(manual_plan_exists)
            self.assertFalse(boundary_file_exists)
            self.assertNotEqual(state.get("QueueState"), "attention")

    def test_explicit_automatable_core_then_external_boundary_rejection_falls_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, runtime = self._run_reader(
                repo,
                self._block(
                    self._invalid_automatable_payload(manual_criteria=True)
                ),
                self._block(self._repo_payload()),
            )
            state = workflow_stages.read_state(current)
            fallback = json.loads(
                (current / boundary.FALLBACK_FILE).read_text(encoding="utf-8")
            )

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(
            state["ExecutionClassificationSource"],
            boundary.OPERATOR_CLASSIFICATION_FALLBACK_SOURCE,
        )
        self.assertIn(
            "automatable classification cannot contain unresolved manual",
            fallback["first_rejection"],
        )
        self.assertIn("external_boundaries", fallback["correction_rejection"])

    def test_explicit_automatable_external_boundary_then_core_rejection_falls_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, runtime = self._run_reader(
                repo,
                self._block(self._repo_payload()),
                self._block(
                    self._invalid_automatable_payload(human_actions=True)
                ),
            )
            state = workflow_stages.read_state(current)
            fallback = json.loads(
                (current / boundary.FALLBACK_FILE).read_text(encoding="utf-8")
            )

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(
            state["ExecutionClassificationSource"],
            boundary.OPERATOR_CLASSIFICATION_FALLBACK_SOURCE,
        )
        self.assertIn("external_boundaries", fallback["first_rejection"])
        self.assertIn(
            "automatable classification cannot contain unresolved manual",
            fallback["correction_rejection"],
        )

    def test_unmarked_invalid_automatable_genuine_external_claim_remains_protocol_exhausted(self):
        payload = self._invalid_automatable_payload(
            manual_criteria=True,
            human_actions=True,
            resume_evidence=True,
            blocks=True,
        )
        payload["reason"] = (
            "Reader claims publisher certificate approval is still required."
        )
        payload["manual_criteria"] = [
            "Complete publisher identity validation and certificate issuance."
        ]
        payload["human_actions"] = [
            "Complete legal identity approval with the certificate provider."
        ]
        payload["resume_evidence"] = [
            "Record the non-secret certificate identifier."
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(
                temp_dir,
                explicit_automatable=False,
            )
            runtime = _ReaderRuntime(
                {
                    "work": self._block(payload),
                    "correction": self._block(payload),
                }
            )
            with patch.object(role_coordinator_runtime, "_prepare_role"):
                with self.assertRaises(
                    role_coordinator_contract.RoleCoordinatorError
                ) as raised:
                    role_coordinator_runtime.run_role(
                        repo,
                        "reader",
                        runtime,
                        self._snapshots(),
                        runner=lambda *_args, **_kwargs: SimpleNamespace(
                            returncode=0,
                            stdout="",
                            stderr="",
                        ),
                        which=lambda _name: "/usr/bin/opencode",
                    )
            fallback_exists = (current / boundary.FALLBACK_FILE).exists()

        self.assertEqual(
            raised.exception.classification,
            role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
        )
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertFalse(fallback_exists)

    def test_explicit_automatable_malformed_genuine_looking_downgrade_cannot_override_operator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, _runtime = self._run_reader(
                repo,
                self._block(self._genuine_payload(mismatch=True)),
                self._block(self._genuine_payload(mismatch=True)),
            )
            state = workflow_stages.read_state(current)

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(
            state["ExecutionClassificationSource"],
            boundary.OPERATOR_FALLBACK_SOURCE,
        )
        self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)

    def test_explicit_automatable_correction_serialization_failure_falls_back_without_external_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, runtime = self._run_reader(
                repo,
                self._block(self._repo_payload()),
                "Substantive Reader correction, but the classification JSON block was malformed or omitted.\n",
            )
            state = workflow_stages.read_state(current)
            reader_text = (current / "reader-brief.md").read_text(encoding="utf-8")
            parsed = execution.parse_reader_classification(
                reader_text,
                (current / "issue.md").read_text(encoding="utf-8"),
            )

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(parsed.classification, execution.AUTOMATABLE)
        self.assertEqual(
            state["ExecutionClassificationSource"],
            boundary.OPERATOR_FALLBACK_SOURCE,
        )

    def test_core_rejection_followed_by_valid_external_boundary_still_safety_downgrades(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, runtime = self._run_reader(
                repo,
                self._block(
                    self._invalid_automatable_payload(manual_criteria=True)
                ),
                self._block(self._genuine_payload(mismatch=False)),
            )
            state = workflow_stages.read_state(current)
            boundary_file_exists = (current / boundary.EXTERNAL_BOUNDARY_FILE).exists()
            fallback_file_exists = (current / boundary.FALLBACK_FILE).exists()

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(
            state["ExecutionClassification"],
            execution.MANUAL_EXTERNAL,
        )
        self.assertEqual(
            state["ExecutionClassificationSource"],
            "reader-safety-downgrade",
        )
        self.assertTrue(boundary_file_exists)
        self.assertFalse(fallback_file_exists)

    def test_valid_external_boundary_correction_still_fails_closed_as_manual(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, runtime = self._run_reader(
                repo,
                self._block(self._repo_payload()),
                self._block(self._genuine_payload(mismatch=False)),
            )
            state = workflow_stages.read_state(current)
            boundary_file_exists = (current / boundary.EXTERNAL_BOUNDARY_FILE).exists()
            fallback_file_exists = (current / boundary.FALLBACK_FILE).exists()

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(
            state["ExecutionClassification"],
            execution.MANUAL_EXTERNAL,
        )
        self.assertEqual(
            state["ExecutionClassificationSource"],
            "reader-safety-downgrade",
        )
        self.assertFalse(state.get("ExecutionClassificationFallback", False))
        self.assertTrue(boundary_file_exists)
        self.assertFalse(fallback_file_exists)

    def test_non_explicit_code_only_issue_has_conservative_automatable_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=False)
            result, _runtime = self._run_reader(
                repo,
                self._block(self._repo_payload()),
                self._block(self._mismatched_repo_payload()),
            )
            state = workflow_stages.read_state(current)

        self.assertEqual(result["state"], "ACCEPTED")
        self.assertEqual(
            state["ExecutionClassificationSource"],
            boundary.DETERMINISTIC_FALLBACK_SOURCE,
        )
        self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
        self.assertNotEqual(state.get("QueueState"), "attention")

    def test_non_explicit_ambiguous_genuine_external_claim_remains_protocol_exhausted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=False)
            runtime = _ReaderRuntime(
                {
                    "work": self._block(self._genuine_payload(mismatch=True)),
                    "correction": self._block(self._genuine_payload(mismatch=True)),
                }
            )
            with patch.object(role_coordinator_runtime, "_prepare_role"):
                with self.assertRaises(
                    role_coordinator_contract.RoleCoordinatorError
                ) as raised:
                    role_coordinator_runtime.run_role(
                        repo,
                        "reader",
                        runtime,
                        self._snapshots(),
                        runner=lambda *_args, **_kwargs: SimpleNamespace(
                            returncode=0,
                            stdout="",
                            stderr="",
                        ),
                        which=lambda _name: "/usr/bin/opencode",
                    )

            diagnostics = json.loads(
                (current / workflow_stages.DIAGNOSTICS_FILE).read_text(
                    encoding="utf-8"
                )
            )
            fallback_file_exists = (current / boundary.FALLBACK_FILE).exists()

        self.assertEqual(
            raised.exception.classification,
            role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
        )
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(diagnostics["protocol_correction_attempts"]["reader"], 1)
        self.assertFalse(fallback_file_exists)

    def test_fallback_checkpoint_can_advance_reader_to_synthesizer_planner_and_implementer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            self._run_reader(
                repo,
                self._block(
                    self._invalid_automatable_payload(manual_criteria=True)
                ),
                self._block(
                    self._invalid_automatable_payload(human_actions=True)
                ),
            )

            active = mappings()
            state = workflow_stages.read_state(current)
            opencode_resume_manifest.create_open_code_manifest(repo, state)
            opencode_resume_checkpoint.checkpoint_role(
                repo,
                "reader",
                [current / "reader-brief.md"],
                active,
            )

            def resume():
                with patch(
                    "automation.workflow_stages.git",
                    return_value=SimpleNamespace(
                        stdout="base-sha\n",
                        returncode=0,
                    ),
                ), patch(
                    "automation.workflow_stages.workspace_changes",
                    return_value=[],
                ), patch(
                    "automation.workflow_stages.source_identity",
                    return_value={
                        "identity": "source-one",
                        "parent_sha": "base-sha",
                        "changes": [],
                    },
                ):
                    return opencode_resume_execution.resume(repo, active)

            after_reader = resume()
            (current / "synthesized-handoff.md").write_text(
                "Synthesized repository handoff.\n",
                encoding="utf-8",
            )
            opencode_resume_checkpoint.checkpoint_role(
                repo,
                "synthesizer",
                [current / "synthesized-handoff.md"],
                active,
            )
            after_synthesizer = resume()
            (current / "plan.md").write_text("Plan checkpoint.\n", encoding="utf-8")
            opencode_resume_checkpoint.checkpoint_role(
                repo,
                "planner",
                [current / "plan.md"],
                active,
            )
            after_planner = resume()

        self.assertEqual(after_reader["next_action"], "synthesizer")
        self.assertEqual(after_synthesizer["next_action"], "planner")
        self.assertEqual(after_planner["next_action"], "implementer")


if __name__ == "__main__":
    unittest.main()

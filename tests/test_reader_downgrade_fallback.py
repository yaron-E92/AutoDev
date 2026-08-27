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
                self._block(self._mismatched_repo_payload()),
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
        self.assertFalse((current / execution.MANUAL_ACTION_PLAN_FILE).exists())
        self.assertFalse((current / boundary.EXTERNAL_BOUNDARY_FILE).exists())
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
        self.assertFalse(
            (current / role_runtime_diagnostics.LAST_FAILURE_FILE).exists()
        )

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

    def test_valid_external_boundary_correction_still_fails_closed_as_manual(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            result, runtime = self._run_reader(
                repo,
                self._block(self._repo_payload()),
                self._block(self._genuine_payload(mismatch=False)),
            )
            state = workflow_stages.read_state(current)

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
        self.assertTrue((current / boundary.EXTERNAL_BOUNDARY_FILE).exists())
        self.assertFalse((current / boundary.FALLBACK_FILE).exists())

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

        self.assertEqual(
            raised.exception.classification,
            role_runtime_diagnostics.FAILURE_ROLE_PROTOCOL_EXHAUSTED,
        )
        self.assertEqual(runtime.calls, ["work", "correction"])
        self.assertEqual(diagnostics["protocol_correction_attempts"]["reader"], 1)
        self.assertFalse((current / boundary.FALLBACK_FILE).exists())

    def test_fallback_checkpoint_can_advance_reader_to_synthesizer_planner_and_implementer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._setup_repo(temp_dir, explicit_automatable=True)
            self._run_reader(
                repo,
                self._block(self._repo_payload()),
                self._block(self._mismatched_repo_payload()),
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

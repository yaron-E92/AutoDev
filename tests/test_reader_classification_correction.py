from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import execution_classification as execution
from automation import execution_classification_hooks
from automation import opencode_adapter_handoff
from automation import opencode_adapter_roles
from automation import workflow_stages
from automation.opencode_adapter_contract import OpenCodeAdapterError


class ReaderClassificationCorrectionTests(unittest.TestCase):
    def _write_protocol_state(self, repo: Path) -> Path:
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        issue_text = "# Issue #6\n\nCreate and manage persistent decision spaces.\n"
        state: dict[str, object] = {
            "Status": "Prepared",
            "IssueNumber": 6,
            "IssueText": issue_text,
            "ProviderProfile": "",
        }
        execution.enable_protocol(state)
        workflow_stages.write_state(current, state)
        (current / "issue.md").write_text(issue_text, encoding="utf-8")
        return current

    def _classification_block(self) -> str:
        return (
            "AUTODEV_EXECUTION_CLASSIFICATION_JSON\n"
            "{\n"
            '  "classification": "automatable",\n'
            '  "reason": "All acceptance criteria can be completed with repository and GitHub actions.",\n'
            '  "autonomous_criteria": ["Implement and verify the requested repository changes."],\n'
            '  "manual_criteria": [],\n'
            '  "human_actions": [],\n'
            '  "resume_evidence": [],\n'
            '  "manual_prerequisite_blocks_implementation": false,\n'
            '  "autonomous_subset_independent": false\n'
            "}\n"
            "END_AUTODEV_EXECUTION_CLASSIFICATION_JSON\n"
        )

    def test_reader_prepare_and_correction_reuse_canonical_classification_contract(self):
        original_prepare = opencode_adapter_handoff._prepare_reader  # type: ignore[attr-defined]
        original_accept = opencode_adapter_roles._accept_role_once  # type: ignore[attr-defined]
        try:
            # Install the hook around a deterministic base prompt. This specifically
            # catches the stale by-value import that previously let prepare_role()
            # bypass the execution-classification wrapper.
            opencode_adapter_handoff._prepare_reader = (  # type: ignore[attr-defined]
                lambda _repo, _current, _issue_text: "base reader prompt\n"
            )
            execution_classification_hooks._install_reader_gate()

            with tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                current = self._write_protocol_state(repo)

                with patch.object(
                    opencode_adapter_roles,
                    "ensure_current_issue",
                    return_value=current,
                ), patch.object(
                    opencode_adapter_roles.opencode_resume_checkpoint,
                    "begin_role",
                ):
                    prompt_path = opencode_adapter_roles.prepare_role("reader", repo, "6")

                prompt = prompt_path.read_text(encoding="utf-8")
                canonical = execution.reader_contract_instructions().strip()
                self.assertIn("base reader prompt", prompt)
                self.assertIn(canonical, prompt)
                self.assertIn(execution.CLASSIFICATION_BLOCK_START, prompt)
                self.assertIn(execution.CLASSIFICATION_BLOCK_END, prompt)

                reader_result = current / "reader-brief.md"
                reader_result.write_text(
                    "Substantive repository analysis with no classification block.\n",
                    encoding="utf-8",
                )

                with self.assertRaises(OpenCodeAdapterError) as first_failure:
                    opencode_adapter_roles.accept_role("reader", repo, reader_result)
                self.assertIn("one correction is allowed", str(first_failure.exception))

                correction = (current / "contract-correction-reader.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn(canonical, correction)
                self.assertIn(execution.CLASSIFICATION_BLOCK_START, correction)
                self.assertIn(execution.CLASSIFICATION_BLOCK_END, correction)
                self.assertIn(
                    "reader execution-classification contract rejected",
                    correction,
                )

                reader_result.write_text(
                    "Substantive repository analysis.\n\n" + self._classification_block(),
                    encoding="utf-8",
                )
                outputs = opencode_adapter_roles.accept_role("reader", repo, reader_result)

                self.assertIn(current / "reader-brief.md", outputs)
                self.assertIn(current / "synthesized-handoff.md", outputs)
                state = workflow_stages.read_state(current)
                self.assertEqual(state["ExecutionClassification"], execution.AUTOMATABLE)
                self.assertEqual(state["ExecutionClassificationSource"], "reader")
                persisted = json.loads(
                    (current / execution.CLASSIFICATION_FILE).read_text(encoding="utf-8")
                )
                self.assertEqual(persisted["classification"], execution.AUTOMATABLE)
        finally:
            opencode_adapter_handoff._prepare_reader = original_prepare  # type: ignore[attr-defined]
            opencode_adapter_roles._accept_role_once = original_accept  # type: ignore[attr-defined]

    def test_non_reader_correction_does_not_receive_reader_classification_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_protocol_state(repo)
            planner_result = current / "plan.md"
            planner_result.write_text("invalid planner output\n", encoding="utf-8")

            with self.assertRaises(OpenCodeAdapterError):
                opencode_adapter_roles._raise_contract_rejection(  # type: ignore[attr-defined]
                    current,
                    "planner",
                    planner_result,
                    OpenCodeAdapterError("planner contract failed"),
                )

            correction = (current / "contract-correction-planner.md").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(execution.CLASSIFICATION_BLOCK_START, correction)
            self.assertNotIn(execution.CLASSIFICATION_BLOCK_END, correction)
            self.assertNotIn("AutoDev execution-classification contract", correction)


if __name__ == "__main__":
    unittest.main()

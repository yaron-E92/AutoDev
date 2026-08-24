from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from automation import (
    execution_classification as execution,
    execution_classification_evidence,
    execution_classification_hooks,
    opencode_github_entrypoint,
    role_coordinator_flow,
    workflow_stages,
)


class ExecutionClassificationHookTests(unittest.TestCase):
    def _prepared_state(self) -> dict[str, object]:
        return {
            "Status": "Prepared",
            "IssueNumber": 176,
            "IssueTitle": "Provision publisher identity",
            "IssueUrl": "https://github.test/owner/repo/issues/176",
            "IssueText": "# Issue\n",
            "RepoFullName": "owner/repo",
            "BranchName": "autodev/issue-176",
            "Base": "main",
            "RunDir": ".autodev-run/current",
            "LastCommitSha": "",
            "PrUrl": "",
            "PrNumber": 0,
            "PrHeadSha": "",
        }

    def _manual_report(self) -> execution.ExecutionReport:
        return execution.ExecutionReport(
            classification=execution.MANUAL_EXTERNAL,
            reason="Publisher identity validation and signing authority provisioning are external.",
            manual_criteria=("Complete publisher identity validation.",),
            human_actions=("Complete the provider identity workflow.",),
            resume_evidence=("Record the non-secret signing profile identifier.",),
            manual_prerequisite_blocks_implementation=True,
        )

    def test_attention_transition_clears_running_and_ready_and_adds_attention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            workflow_stages.write_state(current, self._prepared_state())
            report = self._manual_report()
            gh_calls: list[list[str]] = []

            def fake_gh(_repo, args, **kwargs):
                gh_calls.append(list(args))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(
                execution_classification_hooks.queue_github,
                "ensure_queue_labels",
                return_value=(),
            ), patch.object(workflow_stages, "gh", side_effect=fake_gh):
                payload = execution_classification_hooks._transition_attention(
                    repo,
                    current,
                    report,
                    runner=Mock(),
                )

            state = workflow_stages.read_state(current)
            self.assertEqual(payload["state"], "ATTENTION_REQUIRED")
            self.assertTrue(payload["successful_non_runnable"])
            self.assertEqual(state["Status"], "AttentionRequired")
            self.assertEqual(state["QueueState"], "attention")
            edit = next(call for call in gh_calls if call[:2] == ["issue", "edit"])
            self.assertIn("autodev:running", edit)
            self.assertIn("autodev:ready", edit)
            self.assertIn("autodev:attention", edit)
            self.assertTrue((current / execution.MANUAL_ACTION_PLAN_FILE).is_file())
            self.assertFalse(str(state.get("PrUrl", "")))

    def test_manual_completion_marker_refreshes_issue_and_reacquires_running_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            state = self._prepared_state()
            state["Status"] = "AttentionRequired"
            state["QueueState"] = "attention"
            workflow_stages.write_state(current, state)
            execution.persist_artifacts(current, self._manual_report())
            gh_calls: list[list[str]] = []

            issue = {
                "number": 176,
                "title": "Provision publisher identity",
                "url": "https://github.test/owner/repo/issues/176",
                "body": f"External work completed.\n{execution.MANUAL_EVIDENCE_MARKER}\n",
                "labels": [
                    {"name": "autodev:managed"},
                    {"name": "autodev:attention"},
                ],
            }

            def fake_gh(_repo, args, **kwargs):
                gh_calls.append(list(args))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.object(workflow_stages, "gh_json", return_value=issue), patch.object(
                execution_classification_evidence.queue_github,
                "ensure_queue_labels",
                return_value=(),
            ), patch.object(workflow_stages, "gh", side_effect=fake_gh):
                refreshed = execution_classification_evidence.refresh_manual_completion_evidence(
                    repo,
                    runner=Mock(),
                )

            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertTrue(refreshed.completion_evidence_present)
            self.assertFalse(refreshed.attention_required)
            updated = workflow_stages.read_state(current)
            self.assertEqual(updated["Status"], "ManualEvidenceAccepted")
            self.assertEqual(updated["QueueState"], "running")
            self.assertTrue(updated["ManualCompletionEvidencePresent"])
            self.assertIn(
                execution.MANUAL_EVIDENCE_MARKER,
                (current / "issue.md").read_text(encoding="utf-8"),
            )
            edit = next(call for call in gh_calls if call[:2] == ["issue", "edit"])
            self.assertIn("autodev:attention", edit)
            self.assertIn("autodev:running", edit)

    def test_resume_evidence_refresh_does_not_infer_completion_from_prose(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            workflow_stages.write_state(current, self._prepared_state())
            execution.persist_artifacts(current, self._manual_report())
            issue = {
                "number": 176,
                "title": "Provision publisher identity",
                "url": "https://github.test/owner/repo/issues/176",
                "body": "README updated with instructions; provider provisioning still pending.",
                "labels": [{"name": "autodev:attention"}],
            }

            with patch.object(workflow_stages, "gh_json", return_value=issue), patch.object(
                workflow_stages,
                "gh",
            ) as gh:
                refreshed = execution_classification_evidence.refresh_manual_completion_evidence(
                    repo,
                    runner=Mock(),
                )

            self.assertIsNotNone(refreshed)
            assert refreshed is not None
            self.assertFalse(refreshed.completion_evidence_present)
            self.assertTrue(refreshed.attention_required)
            gh.assert_not_called()

    def test_explicit_attention_stage_never_invokes_a_role_or_implementer(self):
        runtime = SimpleNamespace(
            name="mock",
            role_snapshots=lambda *args, **kwargs: {},
        )
        calls: list[str] = []

        def fake_stage(_repo, name, **kwargs):
            calls.append(name)
            if name == "preflight":
                return {"state": "CONTINUE"}
            if name == "prepare":
                return {
                    "state": "ATTENTION_REQUIRED",
                    "reason": "external identity validation is required",
                    "successful_non_runnable": True,
                }
            self.fail(f"unexpected stage after manual attention: {name}")

        with patch.object(
            role_coordinator_flow.opencode_runtime,
            "install_workflow_guards",
        ), patch.object(
            role_coordinator_flow.role_runtime,
            "select_runtime",
            return_value=(runtime, "test"),
        ), patch.object(role_coordinator_flow, "run_stage", side_effect=fake_stage), patch.object(
            role_coordinator_flow,
            "run_role",
        ) as run_role, patch.object(
            role_coordinator_flow,
            "terminal_payload",
            side_effect=lambda _repo, payload, **kwargs: dict(payload),
        ):
            payload = role_coordinator_flow.coordinate(Path("."), arguments="176")

        self.assertEqual(payload["state"], "ATTENTION_REQUIRED")
        self.assertEqual(calls, ["preflight", "prepare"])
        run_role.assert_not_called()

    def test_attention_prepare_persists_resumable_issue_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            workflow_stages.write_state(current, self._prepared_state())

            def fake_stage(_repo, name, **kwargs):
                self.assertEqual(name, "prepare")
                return {
                    "state": "ATTENTION_REQUIRED",
                    "reason": "external identity validation is required",
                    "successful_non_runnable": True,
                }

            with patch.object(role_coordinator_flow, "run_stage", new=fake_stage), patch.object(
                execution_classification_evidence.opencode_adapter_protocol,
                "_ensure_opencode_protocol",
            ) as ensure_protocol, patch.object(
                role_coordinator_flow.role_resume,
                "has_manifest",
                return_value=False,
            ), patch.object(
                role_coordinator_flow.role_resume,
                "create_manifest",
            ) as create_manifest, patch.object(
                role_coordinator_flow.role_runtime,
                "persist_selection",
            ) as persist_selection:
                execution_classification_evidence._install_attention_prepare_manifest()
                payload = role_coordinator_flow.run_stage(
                    repo,
                    "prepare",
                    runtime_name="opencode",
                )

            self.assertEqual(payload["state"], "ATTENTION_REQUIRED")
            ensure_protocol.assert_called_once()
            self.assertEqual(
                ensure_protocol.call_args.args[0].resolve(),
                current.resolve(),
            )
            create_manifest.assert_called_once()
            self.assertEqual(create_manifest.call_args.kwargs["runtime_name"], "opencode")
            persist_selection.assert_called_once()
            self.assertEqual(
                persist_selection.call_args.args[0].resolve(),
                repo.resolve(),
            )
            self.assertEqual(persist_selection.call_args.kwargs["name"], "opencode")
            self.assertEqual(persist_selection.call_args.kwargs["source"], "selected")
            self.assertTrue(persist_selection.call_args.kwargs["force_manifest"])

    def test_attention_required_is_a_successful_terminal_cli_state(self):
        self.assertIn(
            "ATTENTION_REQUIRED",
            opencode_github_entrypoint.SUCCESSFUL_TERMINAL_STATES,
        )


if __name__ == "__main__":
    unittest.main()

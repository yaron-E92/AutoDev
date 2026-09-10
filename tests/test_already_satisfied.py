from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from automation import already_satisfied, already_satisfied_hooks, run_manifest
from automation.workflow_storage import read_state, write_json, write_state


class AlreadySatisfiedTests(unittest.TestCase):
    def _snapshot(self, role: str) -> dict[str, object]:
        return run_manifest.build_role_snapshot(
            {"model": f"{role}-model", "transport": "test"},
            {"model": f"{role}-model", "runtime": "test"},
        )

    def _repo(self, root: Path, *, candidate: bool = True) -> tuple[Path, Path]:
        repo = root / "repo"
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        issue = (
            "# GitHub Issue #179: Centralize artifacts output\n\n"
            "URL: https://github.example/issues/179\n\n"
            "## Acceptance criteria\n"
            "- Use repository-wide UseArtifactsOutput.\n"
            "- Remove the legacy out/ package consumer.\n"
        )
        plan = (
            "1) Where to look\n- Directory.Build.props\n\n"
            "2) Files / areas likely to touch\n- none\n\n"
            "3) Assumptions\n- prepared base contains the implementation\n\n"
            "4) Plan\n- independently verify the prepared base\n\n"
            "5) Risks / gotchas\n- do not manufacture cleanup\n\n"
            "6) Recommended implementation approach\n"
            "- Option A: no implementation if verification confirms the base\n"
        )
        if candidate:
            plan += already_satisfied.CANDIDATE_MARKER + "\n"
        (current / "issue.md").write_text(issue, encoding="utf-8")
        (current / "plan.md").write_text(plan, encoding="utf-8")
        write_json(
            current / "state.json",
            {
                "Status": "Planned",
                "RepoFullName": "yaron-E92/events",
                "IssueNumber": 179,
                "IssueTitle": "Centralize artifacts output",
                "IssueText": issue,
                "BaseSha": "1710a36fdafbae72819bfe66eb146d5f70b679c1",
                "BranchName": "autodev/issue-179-artifacts",
                "LastCommitSha": "",
                "PrUrl": "",
                "PrNumber": 0,
                "PrHeadSha": "",
                "LocalCheck": "dotnet test",
                "LastLocalCheckPassed": False,
            },
        )
        run_manifest.create_manifest(
            current / run_manifest.MANIFEST_NAME,
            repo_path=repo,
            github_repo="yaron-E92/events",
            issue_number=179,
            mode="pr",
            base_sha="1710a36fdafbae72819bfe66eb146d5f70b679c1",
            branch="autodev/issue-179-artifacts",
            role_snapshots={
                role: self._snapshot(role)
                for role in (
                    "reader",
                    "synthesizer",
                    "planner",
                    "implementer",
                    "fixer",
                    "verifier",
                )
            },
        )
        return repo, current

    def _source(self) -> dict[str, object]:
        return {
            "identity": "prepared-base-source",
            "parent_sha": "1710a36fdafbae72819bfe66eb146d5f70b679c1",
            "changes": [],
        }

    def _local_pass(self, repo: Path, current: Path, state: dict[str, object], *_args, **_kwargs) -> bool:
        state = dict(state)
        state["LastLocalCheckPassed"] = True
        write_state(current, state)
        (current / "local-check.log").write_text("all deterministic checks passed\n", encoding="utf-8")
        return True

    def _semantic_callback(self, current: Path, verdict: str = "pass"):
        def callback() -> None:
            requirements = [
                {
                    "criterion": "Use repository-wide UseArtifactsOutput.",
                    "status": "met" if verdict == "pass" else "missing",
                    "evidence": ["Directory.Build.props enables UseArtifactsOutput"],
                },
                {
                    "criterion": "Remove the legacy out/ package consumer.",
                    "status": "met" if verdict == "pass" else "missing",
                    "evidence": ["workflow search has no out/ package consumer"],
                },
            ]
            (current / "verification-result.json").write_text(
                json.dumps(
                    {
                        "verdict": verdict,
                        "requirements": requirements,
                        "findings": [],
                        "repair_brief": "" if verdict == "pass" else "Implement the missing requirement.",
                    }
                ),
                encoding="utf-8",
            )

        return callback

    def test_events_179_prepared_base_finishes_without_commit_or_pr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            with patch.object(already_satisfied, "source_identity", return_value=self._source()), patch.object(
                already_satisfied.workflow_verification,
                "run_local_check",
                side_effect=self._local_pass,
            ), patch.object(
                already_satisfied.ux_multimodal_runtime,
                "verify_for_semantic_stage",
                return_value=current / "ux-multimodal-result.json",
            ), patch.object(
                already_satisfied.ux_multimodal,
                "load_result",
                return_value={"status": "not-applicable"},
            ), patch.object(
                already_satisfied.workflow_github,
                "gh",
                return_value=Mock(returncode=0, stdout="", stderr=""),
            ):
                outcome = already_satisfied.probe_candidate(
                    repo,
                    semantic_verifier=self._semantic_callback(current),
                )

            state = read_state(current)
            manifest = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)
            report = (current / already_satisfied.REPORT_NAME).read_text(encoding="utf-8")

        self.assertEqual(outcome, "confirmed")
        self.assertEqual(state["Status"], "AlreadySatisfied")
        self.assertEqual(state.get("LastCommitSha", ""), "")
        self.assertEqual(state.get("PrUrl", ""), "")
        self.assertEqual(manifest[already_satisfied.RECORD_KEY]["status"], "confirmed")
        self.assertIn("Outcome: already satisfied on prepared base", report)
        self.assertIn("Repository modified: no", report)
        self.assertIn("Commit: none", report)
        self.assertIn("PR: none", report)
        self.assertIn("UseArtifactsOutput", report)
        self.assertIn("out/ package consumer", report)

    def test_no_candidate_marker_leaves_tiny_required_change_on_normal_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current = self._repo(Path(temp_dir), candidate=False)
            semantic = Mock()
            with patch.object(
                already_satisfied.workflow_verification,
                "run_local_check",
            ) as local_check:
                outcome = already_satisfied.probe_candidate(
                    repo,
                    semantic_verifier=semantic,
                )

        self.assertEqual(outcome, "not-candidate")
        local_check.assert_not_called()
        semantic.assert_not_called()

    def test_deterministic_failure_rejects_candidate_without_consuming_repair_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            state = read_state(current)
            state["LocalRepairAttemptsByFingerprint"] = {"existing": 2}
            state["LocalCheckFailureFingerprint"] = "existing"
            write_state(current, state)
            semantic = Mock()
            with patch.object(already_satisfied, "source_identity", return_value=self._source()), patch.object(
                already_satisfied.workflow_verification,
                "run_local_check",
                return_value=False,
            ):
                outcome = already_satisfied.probe_candidate(
                    repo,
                    semantic_verifier=semantic,
                )
            state = read_state(current)
            record = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)[
                already_satisfied.RECORD_KEY
            ]

        self.assertEqual(outcome, "rejected")
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(state["LocalRepairAttemptsByFingerprint"], {"existing": 2})
        self.assertNotIn("LocalCheckFailureFingerprint", state)
        self.assertEqual(state["Status"], "Planned")
        semantic.assert_not_called()

    def test_semantic_missing_requirement_returns_to_normal_implementation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            with patch.object(already_satisfied, "source_identity", return_value=self._source()), patch.object(
                already_satisfied.workflow_verification,
                "run_local_check",
                side_effect=self._local_pass,
            ):
                outcome = already_satisfied.probe_candidate(
                    repo,
                    semantic_verifier=self._semantic_callback(current, verdict="repair"),
                )
            state = read_state(current)
            record = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)[
                already_satisfied.RECORD_KEY
            ]

        self.assertEqual(outcome, "rejected")
        self.assertEqual(record["status"], "rejected")
        self.assertEqual(state["Status"], "Planned")
        self.assertEqual(state.get("LastCommitSha", ""), "")
        self.assertEqual(state.get("PrUrl", ""), "")

    def test_issue_or_base_change_invalidates_previous_satisfaction_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            record = already_satisfied.ensure_candidate_record(repo)
            state = read_state(current)
            state["AlreadySatisfiedIssueSha256"] = record["issue_sha256"]
            state["AlreadySatisfiedPlanSha256"] = record["plan_sha256"]
            state["AlreadySatisfiedBaseSha"] = record["prepared_base_sha"]
            write_state(current, state)
            manifest_path = current / run_manifest.MANIFEST_NAME
            manifest = run_manifest.load_manifest(manifest_path)
            manifest[already_satisfied.RECORD_KEY]["status"] = "confirmed"
            run_manifest.save_manifest(manifest_path, manifest)
            self.assertTrue(already_satisfied.current_is_confirmed(repo))

            (current / "issue.md").write_text(
                (current / "issue.md").read_text(encoding="utf-8") + "\nNew acceptance requirement.\n",
                encoding="utf-8",
            )
            refreshed = already_satisfied.ensure_candidate_record(repo)
            self.assertEqual(refreshed["status"], "candidate")

            manifest = run_manifest.load_manifest(manifest_path)
            manifest["target"]["base_sha"] = "new-base-sha"
            run_manifest.save_manifest(manifest_path, manifest)
            state = read_state(current)
            state["BaseSha"] = "new-base-sha"
            write_state(current, state)
            refreshed_again = already_satisfied.ensure_candidate_record(repo)

        self.assertEqual(refreshed_again["status"], "candidate")
        self.assertEqual(refreshed_again["prepared_base_sha"], "new-base-sha")

    def test_status_block_is_explicit_for_confirmed_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            record = already_satisfied.ensure_candidate_record(repo)
            manifest_path = current / run_manifest.MANIFEST_NAME
            manifest = run_manifest.load_manifest(manifest_path)
            manifest[already_satisfied.RECORD_KEY]["status"] = "confirmed"
            run_manifest.save_manifest(manifest_path, manifest)
            state = read_state(current)
            state.update(
                {
                    "Status": "AlreadySatisfied",
                    "AlreadySatisfiedBaseSha": record["prepared_base_sha"],
                    "AlreadySatisfiedIssueSha256": record["issue_sha256"],
                    "AlreadySatisfiedPlanSha256": record["plan_sha256"],
                }
            )
            write_state(current, state)

            text = already_satisfied_hooks._already_satisfied_status_text(repo)

        self.assertIn("Outcome: already satisfied on prepared base", text)
        self.assertIn("Repository modified: no", text)
        self.assertIn("Commit: none", text)
        self.assertIn("PR: none", text)

    def test_terminal_payload_is_success_with_no_shipment_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            record = already_satisfied.ensure_candidate_record(repo)
            manifest_path = current / run_manifest.MANIFEST_NAME
            manifest = run_manifest.load_manifest(manifest_path)
            manifest[already_satisfied.RECORD_KEY]["status"] = "confirmed"
            run_manifest.save_manifest(manifest_path, manifest)
            state = read_state(current)
            state.update(
                {
                    "Status": "AlreadySatisfied",
                    "AlreadySatisfiedBaseSha": record["prepared_base_sha"],
                    "AlreadySatisfiedIssueSha256": record["issue_sha256"],
                    "AlreadySatisfiedPlanSha256": record["plan_sha256"],
                }
            )
            write_state(current, state)
            with patch.object(
                already_satisfied,
                "stage_payload",
                return_value={"state": "ALREADY_SATISFIED"},
            ):
                payload = already_satisfied.terminal_payload(repo)

        self.assertEqual(payload["state"], "ALREADY_SATISFIED")
        self.assertFalse(payload["repository_modified"])
        self.assertFalse(payload["commit_exists"])
        self.assertFalse(payload["pr_exists"])
        self.assertEqual(payload["branch"], "")
        self.assertEqual(payload["verification_performed"], ["deterministic", "semantic"])


if __name__ == "__main__":
    unittest.main()

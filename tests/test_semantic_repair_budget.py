from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import opencode_resume, run_manifest, semantic_repair_budget, workflow_stages


REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _semantic_result(message: str = "Resource navigation is still incomplete") -> str:
    return json.dumps(
        {
            "verdict": "repair",
            "requirements": [
                {
                    "criterion": "Resource navigation works end-to-end",
                    "status": "missing",
                    "evidence": ["src/resource.py"],
                },
                {
                    "criterion": "Unrelated behavior remains stable",
                    "status": "met",
                    "evidence": ["tests/test_resource.py"],
                },
            ],
            "findings": [
                {
                    "severity": "blocking",
                    "message": message,
                    "path": "src/resource.py",
                },
                {
                    "severity": "warning",
                    "message": "Nonblocking note",
                    "path": "README.md",
                },
            ],
            "repair_brief": "Wire the Resource route to the persisted navigation target.",
        }
    )


class SemanticRepairBudgetTests(unittest.TestCase):
    def test_fixed_exhaustion_preserves_final_diagnosis_and_classification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            workflow_stages.write_json(
                current / "state.json",
                {
                    "IssueNumber": 42,
                    "BranchName": "autodev/issue-42",
                    "IssueText": "# Issue 42",
                    "VerifiedSourceIdentity": "verified-source-42",
                },
            )
            (current / "issue.md").write_text("# Issue 42\n", encoding="utf-8")
            (current / "verification-result.json").write_text(
                _semantic_result(), encoding="utf-8"
            )

            with (
                patch.dict(
                    os.environ,
                    {
                        semantic_repair_budget.POLICY_ENV: "fixed",
                        semantic_repair_budget.FIXED_LIMIT_ENV: "2",
                    },
                    clear=False,
                ),
                patch("automation.workflow_stages._require_accepted_role"),
            ):
                _, payload = workflow_stages.execute_stage(
                    "semantic",
                    repo,
                    autodev_root=REPO_ROOT,
                    attempt=2,
                )

            state = workflow_stages.read_state(current)
            repair_exists = (current / "verification-repair.md").is_file()

        self.assertEqual(payload["state"], "BLOCKED")
        self.assertEqual(
            payload["failure_classification"],
            semantic_repair_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED,
        )
        self.assertEqual(payload["root_failure_classification"], "code-repairable")
        self.assertEqual(payload["semantic_repair_attempt"], 2)
        self.assertEqual(payload["max_semantic_repair_attempts"], 2)
        self.assertEqual(payload["repair_brief"], "Wire the Resource route to the persisted navigation target.")
        self.assertEqual(
            payload["semantic_requirements"],
            [
                {
                    "criterion": "Resource navigation works end-to-end",
                    "status": "missing",
                    "evidence": ["src/resource.py"],
                }
            ],
        )
        self.assertEqual(len(payload["semantic_findings"]), 1)
        self.assertEqual(payload["semantic_findings"][0]["path"], "src/resource.py")
        self.assertEqual(payload["verification_result"], ".autodev-run/current/verification-result.json")
        self.assertTrue(payload["failure_fingerprint"])
        self.assertEqual(
            state["LastSemanticFailureDetails"]["failure_fingerprint"],
            payload["failure_fingerprint"],
        )
        self.assertTrue(repair_exists)

    def test_failure_fingerprint_is_stable_for_same_result_and_source(self):
        result = json.loads(_semantic_result())
        budget = {"policy": "fixed", "formula_version": 1, "effective_limit": 2}
        first = semantic_repair_budget.failure_details(
            result,
            budget,
            attempt=2,
            verification_result=Path("verification-result.json"),
            repair_artifact=Path("verification-repair.md"),
            verified_source_identity="same-source",
        )
        second = semantic_repair_budget.failure_details(
            result,
            budget,
            attempt=99,
            verification_result=Path("elsewhere.json"),
            repair_artifact=Path("elsewhere.md"),
            verified_source_identity="same-source",
        )
        self.assertEqual(first["failure_fingerprint"], second["failure_fingerprint"])

    def test_adaptive_budget_uses_verified_issue_scoped_text_changes_and_caps_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            _git(repo, "init")
            _git(repo, "config", "user.email", "autodev@example.invalid")
            _git(repo, "config", "user.name", "AutoDev Tests")
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "bin").mkdir()
            (repo / "src" / "feature.py").write_text("before\n", encoding="utf-8")
            (repo / "tests" / "test_feature.py").write_text("before\n", encoding="utf-8")
            (repo / "bin" / "generated.txt").write_text("before\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "base")
            base = _git(repo, "rev-parse", "HEAD")

            (repo / "src" / "feature.py").write_text(
                "\n".join(f"source {index}" for index in range(21)) + "\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_feature.py").write_text(
                "\n".join(f"test {index}" for index in range(21)) + "\n",
                encoding="utf-8",
            )
            (repo / "bin" / "generated.txt").write_text(
                "\n".join(f"generated {index}" for index in range(500)) + "\n",
                encoding="utf-8",
            )
            state = {
                "BaseSha": base,
                "VerifiedParentSha": base,
                "VerifiedChanges": [
                    {"path": "src/feature.py", "status": "modified"},
                    {"path": "tests/test_feature.py", "status": "modified"},
                    {"path": "bin/generated.txt", "status": "modified"},
                ],
            }

            with patch.dict(
                os.environ,
                {
                    semantic_repair_budget.POLICY_ENV: "adaptive",
                    semantic_repair_budget.ADAPTIVE_MIN_ENV: "1",
                    semantic_repair_budget.ADAPTIVE_MAX_ENV: "3",
                    semantic_repair_budget.ADAPTIVE_BASE_ENV: "1",
                    semantic_repair_budget.LINES_PER_ATTEMPT_ENV: "10",
                    semantic_repair_budget.FIXED_LIMIT_ENV: "2",
                },
                clear=False,
            ):
                budget = semantic_repair_budget.resolve_budget(
                    repo,
                    state,
                    attempt=0,
                    fixed_default=1,
                )

        self.assertEqual(budget["policy"], "adaptive")
        self.assertEqual(budget["effective_limit"], 3)
        self.assertEqual(budget["max_attempts"], 3)
        self.assertEqual(budget["inputs"]["skipped_generated_paths"], ["bin/generated.txt"])
        self.assertEqual(budget["inputs"]["eligible_paths"], ["src/feature.py", "tests/test_feature.py"])
        self.assertGreater(budget["inputs"]["weighted_changed_lines"], 0)

    def test_persisted_budget_never_shrinks_and_explicit_limit_can_raise_it(self):
        state = {
            "SemanticRepairBudget": {
                "policy": "fixed",
                "formula_version": 1,
                "configured_limit": 2,
                "fixed_limit_observed": 2,
                "effective_limit": 2,
                "attempts_consumed": 2,
                "inputs": {},
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            with patch.dict(
                os.environ,
                {semantic_repair_budget.FIXED_LIMIT_ENV: "1"},
                clear=False,
            ):
                unchanged = semantic_repair_budget.resolve_budget(
                    repo, state, attempt=2, fixed_default=1
                )
            state["SemanticRepairBudget"] = unchanged
            with patch.dict(
                os.environ,
                {semantic_repair_budget.FIXED_LIMIT_ENV: "5"},
                clear=False,
            ):
                raised = semantic_repair_budget.resolve_budget(
                    repo, state, attempt=2, fixed_default=1
                )

        self.assertEqual(unchanged["effective_limit"], 2)
        self.assertEqual(raised["effective_limit"], 5)
        self.assertEqual(raised["manual_limit_increase"], 5)

    def test_unchanged_bridge_default_does_not_reopen_adaptive_budget(self):
        state = {
            "SemanticRepairBudget": {
                "policy": "adaptive",
                "formula_version": 1,
                "base_attempts": 1,
                "min_attempts": 1,
                "max_attempts": 5,
                "lines_per_attempt": 200,
                "raw_attempts": 1,
                "fixed_limit_observed": 2,
                "effective_limit": 1,
                "attempts_consumed": 1,
                "inputs": {"weighted_changed_lines": 0},
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            with patch.dict(
                os.environ,
                {semantic_repair_budget.FIXED_LIMIT_ENV: "2"},
                clear=False,
            ):
                unchanged = semantic_repair_budget.resolve_budget(
                    repo, state, attempt=1, fixed_default=2
                )
            state["SemanticRepairBudget"] = unchanged
            with patch.dict(
                os.environ,
                {semantic_repair_budget.FIXED_LIMIT_ENV: "4"},
                clear=False,
            ):
                raised = semantic_repair_budget.resolve_budget(
                    repo, state, attempt=1, fixed_default=2
                )

        self.assertEqual(unchanged["effective_limit"], 1)
        self.assertNotIn("manual_limit_increase", unchanged)
        self.assertEqual(raised["effective_limit"], 4)
        self.assertEqual(raised["manual_limit_increase"], 4)

    def test_raising_blocked_budget_reopens_existing_run_at_semantic_fixer_boundary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            state = {
                "IssueNumber": 42,
                "RepoFullName": "example/repo",
                "BaseSha": "base-sha",
                "BranchName": "autodev/issue-42",
                "Status": "Blocked",
                "SemanticRepairBudget": {
                    "policy": "fixed",
                    "formula_version": 1,
                    "configured_limit": 2,
                    "fixed_limit_observed": 2,
                    "effective_limit": 2,
                    "attempts_consumed": 2,
                    "inputs": {},
                },
            }
            workflow_stages.write_json(current / "state.json", state)
            (current / "verification-repair.md").write_text("repair\n", encoding="utf-8")
            manifest_path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                manifest_path,
                repo_path=repo,
                github_repo="example/repo",
                issue_number=42,
                mode="issue-to-pr",
                base_sha="base-sha",
                branch="autodev/issue-42",
                role_snapshots={},
                semantic_verification={
                    "enabled": True,
                    "repair_budget": state["SemanticRepairBudget"],
                },
            )
            manifest = run_manifest.load_manifest(manifest_path)
            manifest["stages"]["semantic-verified"] = {
                "status": "blocked",
                "details": {"attempt": 2},
            }
            details = {
                "attempted_repairs": 2,
                "maximum_repairs": 2,
                "repair_brief": "Finish Resource navigation",
                "repair_artifact": ".autodev-run/current/verification-repair.md",
                "verification_result": ".autodev-run/current/verification-result.json",
                "verified_source_identity": "source-42",
                "failure_fingerprint": "fingerprint-42",
                "requirements": [],
                "findings": [],
                "budget": state["SemanticRepairBudget"],
            }
            manifest["failure"] = {
                "classification": semantic_repair_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED,
                "root_classification": "code-repairable",
                "reason": "budget exhausted",
                "stage": "semantic-verified",
                "fingerprint": "fingerprint-42",
                "details": details,
            }
            run_manifest.save_manifest(manifest_path, manifest)

            with patch.dict(
                os.environ,
                {semantic_repair_budget.FIXED_LIMIT_ENV: "4"},
                clear=False,
            ):
                reopened = semantic_repair_budget.maybe_reopen_exhausted_budget(repo)

            updated_state = workflow_stages.read_state(current)
            updated_manifest = run_manifest.load_manifest(manifest_path)

        self.assertTrue(reopened)
        self.assertEqual(updated_state["Status"], "SemanticRepairRequired")
        self.assertEqual(updated_state["SemanticRepairBudget"]["effective_limit"], 4)
        self.assertEqual(updated_manifest["failure"], {})
        self.assertEqual(
            updated_manifest["stages"]["semantic-verified"]["status"],
            "repair-required",
        )
        self.assertEqual(
            updated_manifest["stages"]["semantic-verified"]["details"]["attempt"],
            2,
        )
        self.assertEqual(
            opencode_resume.resume_action(updated_manifest, updated_state),
            "fixer-semantic",
        )

    def test_run_manifest_failure_hook_preserves_rich_semantic_failure(self):
        semantic_repair_budget.install_run_manifest_hooks()
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                path,
                repo_path=repo,
                github_repo="example/repo",
                issue_number=1,
                mode="issue-to-pr",
                base_sha="base",
                branch="branch",
                role_snapshots={},
            )
            manifest = run_manifest.load_manifest(path)
            manifest["failure"] = {
                "classification": semantic_repair_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED,
                "root_classification": "code-repairable",
                "reason": "rich",
                "stage": "semantic-verified",
                "fingerprint": "abc",
                "details": {"repair_brief": "specific repair"},
            }
            run_manifest.save_manifest(path, manifest)

            run_manifest.record_failure(
                path,
                classification=semantic_repair_budget.FAILURE_REPAIR_BUDGET_EXHAUSTED,
                reason="short reason",
                stage="semantic-verified",
            )
            failure = run_manifest.load_manifest(path)["failure"]

        self.assertEqual(failure["root_classification"], "code-repairable")
        self.assertEqual(failure["fingerprint"], "abc")
        self.assertEqual(failure["details"]["repair_brief"], "specific repair")


if __name__ == "__main__":
    unittest.main()

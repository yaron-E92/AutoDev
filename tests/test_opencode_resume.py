import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import opencode_resume, run_manifest, workflow_stages


REPO_ROOT = Path(__file__).resolve().parents[1]


def mappings(**overrides):
    values = {
        role: {
            "agent": f"autodev-{role}",
            "source": "explicit",
            "model": f"provider/{role}",
            "inherits_from": "",
        }
        for role in opencode_adapter_contract.OPENCODE_ROLE_NAMES
    }
    for role, model in overrides.items():
        values[role] = {
            "agent": f"autodev-{role}",
            "source": "explicit",
            "model": model,
            "inherits_from": "",
        }
    return values


class OpenCodeResumeTests(unittest.TestCase):
    def _repo(self, root: str) -> tuple[Path, Path, dict[str, object]]:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state = {
            "Status": "Prepared",
            "IssueNumber": 63,
            "RepoFullName": "owner/repo",
            "BranchName": "autodev/issue-63",
            "BaseSha": "base-sha",
            "BaseTreeSha": "base-tree",
            "PreparedSnapshotHash": "snapshot",
            "LastCommitSha": "",
            "PrUrl": "",
            "PrNumber": 0,
            "PrHeadSha": "",
        }
        (current / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (current / "issue.md").write_text("# Issue 63\n", encoding="utf-8")
        (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
        opencode_resume.create_open_code_manifest(repo, state)
        return repo, current, state

    def _resume_patches(self, *, source_identity="source-one", workspace_changes=None):
        if workspace_changes is None:
            workspace_changes = []
        return (
            patch(
                "automation.opencode_resume.workflow_stages.git",
                return_value=SimpleNamespace(stdout="base-sha\n", returncode=0),
            ),
            patch(
                "automation.opencode_resume.workflow_stages.workspace_changes",
                return_value=workspace_changes,
            ),
            patch(
                "automation.opencode_resume.workflow_stages.source_identity",
                return_value={
                    "identity": source_identity,
                    "parent_sha": "base-sha",
                    "changes": [{"path": "file.py", "status": "modified", "sha256": "abc"}],
                },
            ),
        )

    def test_planner_checkpoint_resumes_at_implementer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(temp_dir)
            active = mappings()
            (current / "reader-brief.md").write_text("reader\n", encoding="utf-8")
            opencode_resume.checkpoint_role(repo, "reader", [current / "reader-brief.md"], active)
            (current / "synthesized-handoff.md").write_text("handoff\n", encoding="utf-8")
            opencode_resume.checkpoint_role(repo, "synthesizer", [current / "synthesized-handoff.md"], active)
            (current / "plan.md").write_text("plan\n", encoding="utf-8")
            opencode_resume.checkpoint_role(repo, "planner", [current / "plan.md"], active)

            git_patch, changes_patch, source_patch = self._resume_patches()
            with git_patch, changes_patch, source_patch:
                payload = opencode_resume.resume(repo, active)

            self.assertEqual(payload["next_stage"], "implementation-generated")
            self.assertEqual(payload["next_action"], "implementer")
            self.assertEqual(payload["next_role"], "implementer")
            self.assertEqual(payload["next_model"], "provider/implementer")

    def test_accepted_implementation_resumes_at_local_check_without_rerunning_implementer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(temp_dir)
            active = mappings()
            opencode_resume.reconcile_models(repo, active)
            manifest_path = opencode_resume.manifest_path(repo)
            for stage in ("repository-read", "handoff-synthesized", "plan-created"):
                run_manifest.complete_stage(manifest_path, stage, run_root=current)
            (current / "commit-message.txt").write_text("Implement issue 63\n", encoding="utf-8")
            proof = {
                "identity": "source-one",
                "parent_sha": "base-sha",
                "changes": [{"path": "file.py", "status": "modified", "sha256": "abc"}],
            }
            with patch("automation.opencode_resume.workflow_stages.source_identity", return_value=proof):
                opencode_resume.checkpoint_role(repo, "implementer", [current / "commit-message.txt"], active)

            git_patch, changes_patch, source_patch = self._resume_patches(source_identity="source-one")
            with git_patch, changes_patch, source_patch:
                payload = opencode_resume.resume(repo, active)

            self.assertEqual(payload["next_stage"], "deterministic-verified")
            self.assertEqual(payload["next_action"], "local-check")

    def test_post_semantic_resume_goes_directly_to_pr_and_ci(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(temp_dir)
            active = mappings()
            opencode_resume.reconcile_models(repo, active)
            manifest_path = opencode_resume.manifest_path(repo)
            for stage in (
                "repository-read",
                "handoff-synthesized",
                "plan-created",
                "implementation-generated",
            ):
                run_manifest.complete_stage(manifest_path, stage, run_root=current)
            run_manifest.complete_stage(
                manifest_path,
                "patch-applied",
                run_root=current,
                details={"source_identity": "source-one", "parent_sha": "base-sha"},
            )
            (current / "local-check.log").write_text("ok\n", encoding="utf-8")
            run_manifest.complete_stage(
                manifest_path,
                "deterministic-verified",
                run_root=current,
                artifacts=[current / "local-check.log"],
                details={"attempt": 0, "source_identity": "source-one"},
            )
            (current / "verification-result.json").write_text('{"verdict":"pass"}\n', encoding="utf-8")
            run_manifest.complete_stage(
                manifest_path,
                "semantic-verified",
                run_root=current,
                artifacts=[current / "verification-result.json"],
                details={"attempt": 0, "source_identity": "source-one", "verdict": "pass"},
            )

            git_patch, changes_patch, source_patch = self._resume_patches(source_identity="source-one")
            with git_patch, changes_patch, source_patch:
                payload = opencode_resume.resume(repo, active)

            self.assertEqual(payload["next_stage"], "pr-created")
            self.assertEqual(payload["next_action"], "pr-and-ci")

    def test_repair_required_state_persists_counter_and_resumes_at_fixer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _, _ = self._repo(temp_dir)
            path = opencode_resume.manifest_path(repo)
            run_manifest.record_stage_state(
                path,
                "deterministic-verified",
                status="repair-required",
                details={"attempt": 2, "artifact": "local-repair.md"},
            )
            manifest = run_manifest.load_manifest(path)

            self.assertEqual(opencode_resume.resume_action(manifest, {}), "fixer-local")
            self.assertEqual(opencode_resume.repair_attempts(manifest)["local"], 2)

    def test_resume_rejects_patch_identity_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(temp_dir)
            active = mappings()
            opencode_resume.reconcile_models(repo, active)
            path = opencode_resume.manifest_path(repo)
            for stage in (
                "repository-read",
                "handoff-synthesized",
                "plan-created",
                "implementation-generated",
            ):
                run_manifest.complete_stage(path, stage, run_root=current)
            run_manifest.complete_stage(
                path,
                "patch-applied",
                run_root=current,
                details={"source_identity": "expected-source", "parent_sha": "base-sha"},
            )

            git_patch, changes_patch, source_patch = self._resume_patches(source_identity="different-source")
            with git_patch, changes_patch, source_patch:
                with self.assertRaises(opencode_resume.OpenCodeResumeError) as raised:
                    opencode_resume.resume(repo, active)

            self.assertIn("source/worktree drift", str(raised.exception))

    def test_changed_completed_role_requires_explicit_invalidation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(temp_dir)
            original = mappings()
            opencode_resume.reconcile_models(repo, original)
            path = opencode_resume.manifest_path(repo)
            for stage in ("repository-read", "handoff-synthesized", "plan-created"):
                run_manifest.complete_stage(path, stage, run_root=current)
            changed = mappings(planner="provider/new-planner")

            git_patch, changes_patch, source_patch = self._resume_patches()
            with git_patch, changes_patch, source_patch:
                with self.assertRaises(opencode_resume.OpenCodeResumeError) as raised:
                    opencode_resume.resume(repo, changed)
            self.assertIn("--invalidate-role", str(raised.exception))

            git_patch, changes_patch, source_patch = self._resume_patches()
            with git_patch, changes_patch, source_patch:
                payload = opencode_resume.resume(repo, changed, invalidated_roles={"planner"})
            self.assertEqual(payload["next_action"], "planner")

    def test_completed_pr_resume_is_idempotent_and_finishes_without_new_work(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(temp_dir)
            active = mappings()
            opencode_resume.reconcile_models(repo, active)
            path = opencode_resume.manifest_path(repo)
            for stage in run_manifest.PRIMARY_STAGES[:-1]:
                run_manifest.complete_stage(path, stage, run_root=current)
            (current / "ci-summary.json").write_text('{"state":"terminal-success"}\n', encoding="utf-8")
            run_manifest.complete_stage(
                path,
                "pr-created",
                run_root=current,
                artifacts=[current / "ci-summary.json"],
                details={"head_sha": "commit-one", "commit_sha": "commit-one", "attempt": 0},
            )
            state.update(
                {
                    "Status": "ReadyForReview",
                    "LastCommitSha": "commit-one",
                    "CreatedCommitSha": "commit-one",
                    "CreatedTreeSha": "tree-one",
                    "CreatedParentSha": "base-sha",
                    "ShippedSourceIdentity": "source-one",
                    "ShippedTreeVerified": True,
                    "PrUrl": "https://github.com/owner/repo/pull/1",
                    "PrNumber": 1,
                    "PrHeadSha": "commit-one",
                    "CiProof": {"state": "terminal-success", "head_sha": "commit-one", "checks": [{"name": "ci"}]},
                }
            )
            (current / "state.json").write_text(json.dumps(state), encoding="utf-8")

            git_patch, changes_patch, source_patch = self._resume_patches(source_identity="source-one")
            with git_patch, changes_patch, source_patch, patch(
                "automation.opencode_resume.workflow_stages.validate_ready_proof"
            ) as ready_proof:
                payload = opencode_resume.resume(repo, active)

            self.assertEqual(payload["state"], "COMPLETE")
            self.assertEqual(payload["next_action"], "complete")
            self.assertEqual(payload["pr_url"], "https://github.com/owner/repo/pull/1")
            ready_proof.assert_called_once()

    def test_installer_sync_includes_status_and_resume_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            opencode_adapter_assets.install_assets(target, REPO_ROOT)

            self.assertTrue((target / ".opencode" / "commands" / "autodev-status.md").is_file())
            self.assertTrue((target / ".opencode" / "commands" / "autodev-resume.md").is_file())

    def test_status_and_resume_parser_surface_is_portable(self):
        parser = opencode_adapter_cli.build_parser()
        status = parser.parse_args(["status", "--repo", ".", "--invalidate-role", "planner"])
        resume = parser.parse_args(["resume", "--repo", ".", "--invalidate-role", "implementer"])

        self.assertEqual(status.command, "status")
        self.assertEqual(status.invalidate_role, ["planner"])
        self.assertEqual(resume.command, "resume")
        self.assertEqual(resume.invalidate_role, ["implementer"])


if __name__ == "__main__":
    unittest.main()

from automation import opencode_adapter_assets

from automation import opencode_adapter_cli

from automation import opencode_adapter_contract

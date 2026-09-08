from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import revision, run_manifest, workflow_stages


class RevisionTests(unittest.TestCase):
    def _prepared_run(self, repo: Path) -> tuple[Path, Path]:
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state = {
            "Status": "ReadyForReview",
            "RepoFullName": "example/project",
            "IssueNumber": 42,
            "IssueTitle": "Original title",
            "IssueUrl": "https://github.com/example/project/issues/42",
            "IssueText": "",
            "Labels": ["autodev:running"],
            "SemVerIntent": "patch",
            "SemVerIntentSource": "issue",
            "BranchName": "autodev/issue-42",
            "BaseSha": "base-sha",
            "AcceptedRoleArtifacts": {
                role: {"artifact": f"{role}.out", "sha256": "old"}
                for role in ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
            },
            "LastLocalCheckPassed": True,
            "PrUrl": "https://github.com/example/project/pull/99",
            "PrNumber": 99,
            "PrHeadSha": "old-pr-head",
            "CiProof": {"state": "terminal-success"},
            "VerifiedParentSha": "old-parent",
            "VerifiedSourceIdentity": "old-source",
            "VerifiedChanges": ["old.py"],
            "ShippedSourceIdentity": "old-shipped",
            "LastCommitSnapshotHash": "old-snapshot",
        }
        issue = (
            "# GitHub Issue #42: Original title\n\n"
            "URL: https://github.com/example/project/issues/42\n\n"
            "Original requirements.\n"
        )
        state["IssueText"] = issue
        (current / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (current / "issue.md").write_text(issue, encoding="utf-8")
        (current / "reader-brief.md").write_text("reader evidence\n", encoding="utf-8")
        (current / "synthesized-handoff.md").write_text("old synthesis\n", encoding="utf-8")
        (current / "plan.md").write_text("old plan\n", encoding="utf-8")
        (current / "commit-message.txt").write_text("old commit\n", encoding="utf-8")
        (current / "verification-result.json").write_text("{}\n", encoding="utf-8")

        manifest_path = current / run_manifest.MANIFEST_NAME
        run_manifest.create_manifest(
            manifest_path,
            repo_path=repo,
            github_repo="example/project",
            issue_number=42,
            mode="issue-to-pr",
            base_sha="base-sha",
            branch="autodev/issue-42",
            role_snapshots={
                role: {"fingerprint": f"fp-{role}", "safe_metadata": {}}
                for role in ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
            },
        )
        run_manifest.complete_stage(
            manifest_path,
            "issue-selected",
            run_root=current,
            artifacts=[current / "issue.md"],
        )
        run_manifest.complete_stage(
            manifest_path,
            "repository-read",
            run_root=current,
            artifacts=[current / "reader-brief.md"],
        )
        run_manifest.complete_stage(
            manifest_path,
            "handoff-synthesized",
            run_root=current,
            artifacts=[current / "synthesized-handoff.md"],
        )
        run_manifest.complete_stage(
            manifest_path,
            "plan-created",
            run_root=current,
            artifacts=[current / "plan.md"],
        )
        run_manifest.complete_stage(
            manifest_path,
            "implementation-generated",
            run_root=current,
            artifacts=[current / "commit-message.txt"],
        )
        run_manifest.complete_stage(manifest_path, "patch-applied", run_root=current)
        run_manifest.complete_stage(manifest_path, "deterministic-verified", run_root=current)
        run_manifest.complete_stage(
            manifest_path,
            "semantic-verified",
            run_root=current,
            artifacts=[current / "verification-result.json"],
        )
        return current, manifest_path

    def _source_patch(self):
        return patch.object(
            workflow_stages,
            "source_identity",
            return_value={
                "identity": "source-at-revision",
                "parent_sha": "base-sha",
                "changes": ["existing.py"],
            },
        )

    def test_manual_revision_archives_and_invalidates_from_synthesizer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, manifest_path = self._prepared_run(repo)
            with patch.object(revision, "_git_stdout", return_value="head-sha"), self._source_patch():
                record = revision.begin_revision(
                    repo,
                    instructions="Keep the useful implementation but remove release orchestration.",
                )

            manifest = run_manifest.load_manifest(manifest_path)
            self.assertEqual(
                manifest["completed_stages"],
                ["issue-selected", "repository-read"],
            )
            self.assertEqual(record["trigger"], "manual")
            self.assertEqual(record["implementation_source_identity"], "source-at-revision")
            self.assertIn("handoff-synthesized", record["superseded_stages"])
            revision_root = current / "revisions" / str(record["revision_id"])
            self.assertEqual(
                (revision_root / "superseded/synthesized-handoff.md").read_text(encoding="utf-8"),
                "old synthesis\n",
            )
            self.assertEqual(
                (revision_root / "superseded/plan.md").read_text(encoding="utf-8"),
                "old plan\n",
            )
            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(set(state["AcceptedRoleArtifacts"]), {"reader"})
            self.assertEqual(state["Status"], "Prepared")
            self.assertFalse(state["LastLocalCheckPassed"])
            self.assertEqual(state["PrHeadSha"], "")
            self.assertEqual(state["PrUrl"], "https://github.com/example/project/pull/99")

    def test_refresh_issue_is_explicit_authority_adoption_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, manifest_path = self._prepared_run(repo)
            refreshed = {
                "number": 42,
                "title": "Updated title",
                "url": "https://github.com/example/project/issues/42",
                "body": "Updated authoritative requirements.\n\n+semver: minor",
                "labels": [{"name": "autodev:running"}, {"name": "ux"}],
            }
            with patch.object(revision, "_fetch_issue", return_value=refreshed), patch.object(
                revision, "_git_stdout", return_value="head-sha"
            ), self._source_patch(), patch.object(
                revision.semver_intent,
                "repository_default",
                return_value="patch",
            ):
                record = revision.begin_revision(repo, refresh_issue=True)

            self.assertTrue(record["issue_changed"])
            self.assertNotEqual(record["previous_issue_sha256"], record["refreshed_issue_sha256"])
            issue_text = (current / "issue.md").read_text(encoding="utf-8")
            self.assertIn("Updated authoritative requirements.", issue_text)
            state = json.loads((current / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["IssueTitle"], "Updated title")
            self.assertEqual(state["SemVerIntent"], "minor")
            self.assertEqual(state["Labels"], ["autodev:running", "ux"])
            revision_root = current / "revisions" / str(record["revision_id"])
            self.assertIn("Original requirements.", (revision_root / "issue-before.md").read_text(encoding="utf-8"))
            manifest = run_manifest.load_manifest(manifest_path)
            issue_record = manifest["stages"]["issue-selected"]
            self.assertTrue(issue_record.get("history"))
            self.assertEqual(
                issue_record["artifacts"]["issue.md"],
                run_manifest.hash_file(current / "issue.md"),
            )

    def test_revision_prompts_preserve_existing_work_and_require_delta_plan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._prepared_run(repo)
            with patch.object(revision, "_git_stdout", return_value="head-sha"), self._source_patch():
                revision.begin_revision(repo, instructions="Change only the API boundary.")

            synthesizer = revision.role_prompt_context(repo, "synthesizer")
            planner = revision.role_prompt_context(repo, "planner")
            implementer = revision.role_prompt_context(repo, "implementer")
            self.assertIn("Operator revision instructions (authoritative)", synthesizer)
            self.assertIn("REVISION_CONFLICT:", synthesizer)
            self.assertIn("PRESERVE", planner)
            self.assertIn("REMOVE/REVERT", planner)
            self.assertIn("delta plan", implementer)
            self.assertNotIn("operator-directed revision", revision.role_prompt_context(repo, "reader"))

    def test_irreconcilable_synthesizer_conflict_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._prepared_run(repo)
            with patch.object(revision, "_git_stdout", return_value="head-sha"), self._source_patch():
                revision.begin_revision(repo, instructions="Conflicting operator requirement")

            with self.assertRaises(revision.RevisionError) as raised:
                revision.validate_synthesizer_result(
                    repo,
                    "# Revised handoff\nREVISION_CONFLICT: issue requires A while operator requires not-A\n",
                )
            self.assertIn("unresolved authority conflict", str(raised.exception))

    def test_record_role_use_persists_revision_role_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, _ = self._prepared_run(repo)
            with patch.object(revision, "_git_stdout", return_value="head-sha"), self._source_patch():
                record = revision.begin_revision(repo, instructions="Refine implementation")

            revision.record_role_use(repo, "planner", {"fingerprint": "planner-new"})
            active = revision.load_active(repo)
            self.assertEqual(active["revised_role_fingerprints"]["planner"], "planner-new")
            self.assertEqual(active["current_revised_stage"], "implementer")
            history = json.loads(
                (current / "revisions" / str(record["revision_id"]) / "record.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(history["revised_role_fingerprints"]["planner"], "planner-new")


if __name__ == "__main__":
    unittest.main()

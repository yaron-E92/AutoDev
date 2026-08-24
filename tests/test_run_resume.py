import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from area_reader import workflow as area_runner
from automation import run_real_issue
from automation.model_providers import ModelConfig
from automation.run_manifest import (
    complete_stage,
    create_manifest,
    load_manifest,
    record_failure,
)


class RunResumeTests(unittest.TestCase):
    def _manifest(self, root: Path, *, mode="pr", roles=None):
        path = root / "run-manifest.json"
        (root / "repo").mkdir(exist_ok=True)
        create_manifest(
            path,
            repo_path=root / "repo",
            github_repo="owner/repo",
            issue_number=37,
            mode=mode,
            base_sha="base-sha",
            branch="autodev/issue-37-resume",
            role_snapshots=roles or {},
        )
        return path

    def _artifact(self, root: Path, name: str, text="artifact"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_resume_options_are_removed_before_core_argument_parsing(self):
        values, resume_dir, status, invalidated = run_real_issue._extract_resume_options(
            [
                "--resume",
                "run-dir",
                "--invalidate-role",
                "planner",
                "--status",
            ]
        )

        self.assertEqual(values, [])
        self.assertEqual(resume_dir, Path("run-dir").resolve())
        self.assertTrue(status)
        self.assertEqual(invalidated, {"planner"})

    def test_resume_injects_target_and_preserved_run_options(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._manifest(root, mode="implement")
            manifest = load_manifest(manifest_path)
            manifest["target"]["provider_config_path"] = str(root / "providers.json")
            manifest["target"]["options"] = {
                "max_fix_attempts": 3,
                "debug_artifacts": True,
                "skip_implementation": False,
                "dry_run_implementation": False,
                "baseline_verify": True,
                "managed_labels": True,
            }

            values = run_real_issue._inject_resume_arguments([], root, manifest)

        self.assertIn("--repo", values)
        self.assertIn("--github-repo", values)
        self.assertIn("--issue", values)
        self.assertIn("--out", values)
        self.assertIn("--mode", values)
        self.assertIn("--provider-config", values)
        self.assertIn("--max-fix-attempts", values)
        self.assertIn("--debug-artifacts", values)
        self.assertIn("--baseline-verify", values)
        self.assertIn("--manage-labels", values)

    def test_status_mode_reads_manifest_without_running_workflow(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._manifest(root, mode="plan-only")
            artifact = self._artifact(root, "issue.md")
            complete_stage(manifest_path, "issue-selected", run_root=root, artifacts=[artifact])
            stdout = io.StringIO()
            stderr = io.StringIO()

            code = run_real_issue.run(
                ["--resume", str(root), "--status"],
                stdout=stdout,
                stderr=stderr,
            )

        self.assertEqual(code, 0)
        self.assertIn("Run ID:", stdout.getvalue())
        self.assertIn("Next stage: repository-read", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_apply_patch_is_idempotent_when_reverse_check_succeeds(self):
        patch = Path("patch.diff")
        reverse = run_real_issue.CommandResult(
            ["git", "apply", "--check", "--reverse", str(patch)],
            Path("."),
            0,
            "",
            "",
        )

        with mock.patch.object(run_real_issue, "run_command", return_value=reverse) as run_command:
            applied = run_real_issue.apply_patch_file(Path("."), patch, io.StringIO())

        self.assertFalse(applied)
        self.assertEqual(run_command.call_count, 1)
        self.assertIn("--reverse", run_command.call_args.args[0])

    def test_existing_pr_lookup_fails_closed_on_unknown_state(self):
        failure = run_real_issue.CommandResult(
            ["gh", "pr", "list"],
            Path("."),
            1,
            "",
            "authentication failed",
        )

        with mock.patch.object(run_real_issue, "run_command", return_value=failure):
            with self.assertRaises(run_real_issue.RunnerError) as raised:
                run_real_issue._find_existing_pr(
                    Path("."),
                    "owner/repo",
                    "autodev/issue-37-resume",
                    io.StringIO(),
                )

        self.assertIn("duplicate-PR risk", str(raised.exception))

    def test_existing_pr_is_reused_from_structured_lookup(self):
        result = run_real_issue.CommandResult(
            ["gh", "pr", "list"],
            Path("."),
            0,
            json.dumps(
                [
                    {
                        "number": 123,
                        "url": "https://github.com/owner/repo/pull/123",
                        "state": "OPEN",
                        "isDraft": True,
                    }
                ]
            ),
            "",
        )

        with mock.patch.object(run_real_issue, "run_command", return_value=result):
            pr = run_real_issue._find_existing_pr(
                Path("."),
                "owner/repo",
                "autodev/issue-37-resume",
                io.StringIO(),
            )

        self.assertEqual(pr["number"], 123)
        self.assertEqual(pr["url"], "https://github.com/owner/repo/pull/123")

    def test_semantic_repair_invalidation_removes_stale_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._manifest(root)
            artifact = self._artifact(root, "artifact.txt")
            for stage in (
                "patch-applied",
                "deterministic-verified",
                "semantic-verified",
                "pr-created",
            ):
                complete_stage(manifest_path, stage, run_root=root, artifacts=[artifact])

            run_real_issue._clear_completed_stages(
                manifest_path,
                ["deterministic-verified", "semantic-verified", "pr-created"],
                "semantic repair patch",
            )
            manifest = load_manifest(manifest_path)

        self.assertIn("patch-applied", manifest["completed_stages"])
        self.assertNotIn("deterministic-verified", manifest["completed_stages"])
        self.assertNotIn("semantic-verified", manifest["completed_stages"])
        self.assertNotIn("pr-created", manifest["completed_stages"])
        self.assertTrue(any(item["reason"] == "semantic repair patch" for item in manifest["invalidations"]))

    def test_deterministic_checkpoint_matches_only_current_patch_worktree(self):
        manifest = {
            "completed_stages": ["patch-applied", "deterministic-verified"],
            "stages": {
                "patch-applied": {"details": {"worktree_hash": "same"}},
                "deterministic-verified": {"details": {"worktree_hash": "same"}},
            },
        }

        self.assertTrue(run_real_issue._deterministic_matches_current_patch(manifest))
        manifest["stages"]["patch-applied"]["details"]["worktree_hash"] = "new"
        self.assertFalse(run_real_issue._deterministic_matches_current_patch(manifest))

    def test_area_reader_replays_completed_reader_without_provider_call(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            area_out = root / "area-reader-debug"
            brief = self._artifact(area_out, "area-docs/reader-brief.md", "saved reader brief")
            manifest_path = self._manifest(root)
            complete_stage(manifest_path, "repository-read", run_root=root, artifacts=[brief])
            original_configs = area_runner._ACTIVE_CONFIGS
            original_out = area_runner._ACTIVE_OUT
            original_manifest = area_runner._ACTIVE_MANIFEST
            try:
                area_runner._ACTIVE_CONFIGS = {
                    "reader": ModelConfig(provider="mock", model="reader"),
                    "synthesizer": None,
                    "planner": None,
                    "implementer": None,
                    "fixer": None,
                    "verifier": None,
                }
                area_runner._ACTIVE_OUT = area_out
                area_runner._ACTIVE_MANIFEST = manifest_path
                with mock.patch.object(area_runner, "create_provider") as create_provider:
                    result, elapsed = area_runner.call_provider(
                        None,
                        "reader",
                        "You are the area reader model for area: docs.\nOriginal issue:\nIssue",
                        100,
                    )
            finally:
                area_runner._ACTIVE_CONFIGS = original_configs
                area_runner._ACTIVE_OUT = original_out
                area_runner._ACTIVE_MANIFEST = original_manifest

        self.assertEqual(result["message"]["content"], "saved reader brief")
        self.assertTrue(result["resumed"])
        self.assertEqual(elapsed, 0.0)
        create_provider.assert_not_called()

    def test_provider_failure_classification_remains_available_for_resume_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = self._manifest(root)
            record_failure(
                manifest_path,
                classification="rate_limited",
                reason="provider quota exhausted",
                stage="implementation-generated",
            )

            stdout = io.StringIO()
            code = run_real_issue.run(
                ["--resume", str(root), "--status"],
                stdout=stdout,
                stderr=io.StringIO(),
            )

        self.assertEqual(code, 0)
        self.assertIn("Last run failure: rate_limited", stdout.getvalue())

    def test_next_stage_credentials_are_checked_without_requiring_completed_roles(self):
        manifest = {
            "target": {"mode": "pr"},
            "completed_stages": [
                "issue-selected",
                "repository-read",
                "handoff-synthesized",
                "plan-created",
            ],
        }
        implementer = ModelConfig(
            provider="openai-compatible-chat-completions",
            model="model",
            base_url="https://provider.example/v1",
            api_key_env="MISSING_TEST_KEY",
        )

        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(run_real_issue.RunnerError) as raised:
                run_real_issue._validate_next_stage_provider(
                    manifest,
                    {"implementer": implementer},
                )

        self.assertIn("MISSING_TEST_KEY", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

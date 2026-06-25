import io
import tempfile
import unittest
from pathlib import Path

from automation import run_real_issue
from automation.model_providers import ModelConfig, MockProvider


class RunRealIssueTests(unittest.TestCase):
    def test_issue_branch_name_uses_autodev_prefix(self):
        issue_text = "# GitHub Issue #18: Add cross-platform real-issue runner!\n"

        branch = run_real_issue.issue_branch_name(18, issue_text)

        self.assertEqual(branch, "autodev/issue-18-add-cross-platform-real-issue-runner")

    def test_fetch_issue_text_formats_autodev_label_json(self):
        issue_text = run_real_issue.issue_text_from_json(
            7,
            "owner/repo",
            {
                "title": "Fix runner",
                "body": "Body text",
                "url": "https://example.test/1",
                "labels": [{"name": "autodev:ready"}, {"name": "area:python"}],
            },
        )

        self.assertIn("# GitHub Issue #7: Fix runner", issue_text)
        self.assertIn("Labels: autodev:ready, area:python", issue_text)
        self.assertIn("Body text", issue_text)

    def test_build_run_summary_uses_routing_and_recommendations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            (out_dir / "routed-areas.json").write_text('{"areas":["ci","docs"]}', encoding="utf-8")
            (out_dir / "recommended-command-groups.json").write_text(
                '{"recommended_command_groups":["env","markdown-smoke"]}',
                encoding="utf-8",
            )

            summary = run_real_issue.build_run_summary(out_dir)

        self.assertIn("Routed areas: ci, docs", summary)
        self.assertIn("Recommended verification groups: env, markdown-smoke", summary)

    def test_extract_patch_from_markers(self):
        response = """BEGIN_UNIFIED_DIFF
diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-old
+new
END_UNIFIED_DIFF"""

        patch = run_real_issue.extract_unified_diff(response)

        self.assertIn("diff --git a/a.txt b/a.txt", patch)

    def test_no_changes_required_detection(self):
        explanation = run_real_issue.parse_no_changes_required("NO_CHANGES_REQUIRED\nAlready done")

        self.assertEqual(explanation, "Already done")

    def test_implementation_prompt_includes_output_contract(self):
        prompt = run_real_issue.build_implementation_prompt(
            issue_text="Issue",
            synthesized_handoff="Handoff",
            coder_plan="Plan",
            recommended_command_groups="{}",
            constraints="Constraints",
            branch_name="autodev/issue-1-test",
        )

        self.assertIn("BEGIN_UNIFIED_DIFF", prompt)
        self.assertIn("NO_CHANGES_REQUIRED", prompt)
        self.assertIn("minimal, issue-scoped changes", prompt)

    def test_select_next_issue_uses_oldest_and_excludes_running_blocked(self):
        issues = [
            {
                "number": 3,
                "title": "Newer",
                "url": "u3",
                "createdAt": "2026-01-03T00:00:00Z",
                "labels": [{"name": "autodev:ready"}],
            },
            {
                "number": 2,
                "title": "Running",
                "url": "u2",
                "createdAt": "2026-01-02T00:00:00Z",
                "labels": [{"name": "autodev:ready"}, {"name": "autodev:running"}],
            },
            {
                "number": 1,
                "title": "Oldest",
                "url": "u1",
                "createdAt": "2026-01-01T00:00:00Z",
                "labels": [{"name": "autodev:ready"}],
            },
        ]

        selected = run_real_issue.select_next_issue(
            issues,
            running_label="autodev:running",
            blocked_label="autodev:blocked",
            selection="oldest",
        )

        self.assertEqual(selected.number, 1)

    def test_label_lifecycle_uses_autodev_labels(self):
        calls = []

        def fake_run(argv, *, cwd, stream, check=True, timeout=None, input_text=None):
            calls.append(argv)
            return run_real_issue.CommandResult(argv, cwd, 0, "", "")

        original = run_real_issue.run_command
        try:
            run_real_issue.run_command = fake_run
            run_real_issue.update_issue_labels(
                Path("."),
                "owner/repo",
                5,
                add=["autodev:running"],
                remove=["autodev:failed"],
                stream=io.StringIO(),
            )
        finally:
            run_real_issue.run_command = original

        self.assertIn("--add-label", calls[0])
        self.assertIn("autodev:running", calls[0])
        self.assertIn("--remove-label", calls[1])
        self.assertIn("autodev:failed", calls[1])

    def test_pr_mode_refuses_to_commit_run_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            out_dir = repo / ".autodev-run"
            out_dir.mkdir()
            original_changed = run_real_issue.changed_worktree_paths
            original_run = run_real_issue.run_command
            try:
                run_real_issue.changed_worktree_paths = lambda repo, stream: [".autodev-run/issue.md"]
                run_real_issue.run_command = (
                    lambda argv, *, cwd, stream, check=True, timeout=None, input_text=None:
                    run_real_issue.CommandResult(argv, cwd, 0, "autodev/issue-1-test\n", "")
                )
                with self.assertRaises(run_real_issue.RunnerError):
                    run_real_issue.create_draft_pr(
                        repo,
                        "owner/repo",
                        1,
                        "# GitHub Issue #1: Test",
                        out_dir,
                        ModelConfig(provider="mock", model="reader"),
                        ModelConfig(provider="mock", model="coder"),
                        io.StringIO(),
                    )
            finally:
                run_real_issue.changed_worktree_paths = original_changed
                run_real_issue.run_command = original_run

    def test_dry_run_implementation_calls_coder_and_saves_patch(self):
        response = """BEGIN_UNIFIED_DIFF
diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
END_UNIFIED_DIFF"""
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            (out_dir / "synthesized-handoff.md").write_text("handoff", encoding="utf-8")
            (out_dir / "coder-plan.md").write_text("plan", encoding="utf-8")
            (out_dir / "recommended-command-groups.json").write_text("{}", encoding="utf-8")
            provider = MockProvider([response])

            result = run_real_issue.run_implementation_loop(
                repo=out_dir,
                out_dir=out_dir,
                issue_text="Issue",
                branch_name="autodev/issue-1-test",
                coder_provider=provider,
                coder_config=ModelConfig(provider="mock", model="coder"),
                max_fix_attempts=0,
                dry_run=True,
                stream=io.StringIO(),
            )

        self.assertTrue(result.passed)
        self.assertEqual(len(provider.prompts), 1)

    def test_verification_summary_is_written_on_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            out_dir = Path(temp_dir) / "out"
            out_dir.mkdir()
            (out_dir / "recommended-command-groups.json").write_text(
                '{"recommended_command_groups":["fail"]}',
                encoding="utf-8",
            )
            (out_dir / "verification-command-groups.json").write_text(
                '[{"name":"fail","manual":false,"commands":[{"argv":["python3","-c","import sys; sys.exit(2)"],"cwd":".","optional":false}]}]',
                encoding="utf-8",
            )

            result = run_real_issue.run_recommended_verification(out_dir, repo, 0, io.StringIO())

            self.assertFalse(result.passed)
            self.assertTrue((out_dir / "verification" / "attempt-0.md").is_file())

    def test_plan_only_uses_reader_provider_for_planning_not_coder_provider(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            out_dir = repo / "out"
            captured = {}
            originals = {
                "require_tools": run_real_issue.require_tools,
                "select_issue": run_real_issue.select_issue,
                "fetch_issue_text": run_real_issue.fetch_issue_text,
                "ensure_clean_worktree": run_real_issue.ensure_clean_worktree,
                "ensure_issue_branch": run_real_issue.ensure_issue_branch,
                "run_area_reader": run_real_issue.run_area_reader,
                "write_operational_outputs": run_real_issue.write_operational_outputs,
            }
            try:
                run_real_issue.require_tools = lambda tools: None
                run_real_issue.select_issue = lambda args, repo, stream: run_real_issue.IssueSelection(1, "T", "u", [])
                run_real_issue.fetch_issue_text = lambda github_repo, issue, repo, stream: "# GitHub Issue #1: T\n"
                run_real_issue.ensure_clean_worktree = lambda repo, stream: None
                run_real_issue.ensure_issue_branch = lambda repo, branch, stream: None

                def fake_area_reader(repo, issue_text, reader_config, coder_config, area_out, stream):
                    captured["reader"] = reader_config
                    captured["coder"] = coder_config

                run_real_issue.run_area_reader = fake_area_reader
                run_real_issue.write_operational_outputs = lambda issue_text, area_out, out_dir, keep_debug: None

                code = run_real_issue.run(
                    [
                        "--repo",
                        str(repo),
                        "--github-repo",
                        "owner/repo",
                        "--issue",
                        "1",
                        "--mode",
                        "plan-only",
                        "--out",
                        str(out_dir),
                        "--reader-provider",
                        "mock",
                        "--reader-model",
                        "reader",
                        "--coder-provider",
                        "mock",
                        "--coder-model",
                        "coder",
                    ],
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            finally:
                for name, value in originals.items():
                    setattr(run_real_issue, name, value)

        self.assertEqual(code, 0)
        self.assertEqual(captured["reader"].model, "reader")
        self.assertEqual(captured["coder"].model, "reader")


if __name__ == "__main__":
    unittest.main()

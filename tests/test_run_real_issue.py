import io
import json
import sys
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

    def test_area_reader_planner_prompt_uses_synthesis_and_labels_as_hints(self):
        prompt = run_real_issue.build_area_reader_planner_prompt(
            issue_text="Fix expiring entries",
            local_check="dotnet test",
            labels=["area:maui", "area:api"],
            profile_context_hints="MAUI profile text\nAPI profile text",
            routed_areas={"areas": ["maui"]},
            synthesized_handoff="Area-reader narrowed this to the MAUI list view.",
            coder_plan="Inspect ExpiringEntriesPage.xaml and its view model.",
            relevant_files=["src/App/ExpiringEntriesPage.xaml", "src/App/ExpiringEntriesViewModel.cs"],
            recommended_command_groups={"recommended_command_groups": ["dotnet-test"]},
            workspace_snapshot={"src/App/ExpiringEntriesPage.xaml": "abc", "src/App/ExpiringEntriesViewModel.cs": "def"},
        )

        self.assertIn("Area-reader synthesized handoff", prompt)
        self.assertIn("Area-reader narrowed this to the MAUI list view.", prompt)
        self.assertIn("Routing hints only", prompt)
        self.assertIn("GitHub labels: area:maui, area:api", prompt)
        self.assertIn("Treat labels and profile text as routing hints only", prompt)
        self.assertIn("src/App/ExpiringEntriesPage.xaml", prompt)
        self.assertIn("Workspace snapshot grounding", prompt)

    def test_area_reader_relevant_files_are_grounded_in_workspace_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir)
            (current / "area-reader-summary.json").write_text(
                json.dumps(
                    {
                        "area_metadata": {
                            "maui": {
                                "included_files": [
                                    "src/App/ExpiringEntriesPage.xaml",
                                    "src/App/Missing.xaml",
                                ]
                            }
                        },
                        "detected_facts": {"maui_projects": ["src/App/App.csproj"]},
                    }
                ),
                encoding="utf-8",
            )
            (current / "detected-facts.json").write_text("{}", encoding="utf-8")
            snapshot = {
                "src/App/ExpiringEntriesPage.xaml": "abc",
                "src/App/App.csproj": "def",
            }

            files = run_real_issue.collect_area_reader_relevant_files(current, snapshot)

        self.assertEqual(files, ["src/App/App.csproj", "src/App/ExpiringEntriesPage.xaml"])

    def test_linux_prepare_uses_area_reader_prompt_helper(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = (repo_root / "linux" / "scripts" / "prepare-next-ready-issue.sh").read_text(encoding="utf-8")

        self.assertIn("prepare_planner_prompt.py", script)
        self.assertIn("--labels-json", script)
        self.assertIn("workspace-snapshot.json", script)
        self.assertNotIn('render_file "$PROMPT_DIR/planner.md"', script)

    def test_operational_outputs_sanitize_model_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            area_out = root / "area"
            current = root / "current"
            area_out.mkdir()
            (area_out / "routing.json").write_text('{"areas":["docs"]}', encoding="utf-8")
            (area_out / "synthesis-brief.md").write_text("\x1b[?25lFinal\b handoff\x07", encoding="utf-8")
            (area_out / "coder-plan.md").write_text("Plan\x1b[0m\x00 text", encoding="utf-8")
            (area_out / "recommended-command-groups.json").write_text(
                '{"recommended_command_groups":["markdown-smoke"]}',
                encoding="utf-8",
            )

            run_real_issue.write_operational_outputs("Issue", area_out, current, keep_debug=True)

            self.assertEqual((current / "synthesized-handoff.md").read_text(encoding="utf-8"), "Final handoff\n")
            self.assertEqual((current / "coder-plan.md").read_text(encoding="utf-8"), "Plan text\n")

    def test_invalid_synthesized_handoff_is_not_fed_to_planner(self):
        prompt = run_real_issue.build_area_reader_planner_prompt(
            issue_text="Document architecture boundary",
            local_check="bash verify.sh",
            labels=["area:maui", "area:docs"],
            profile_context_hints="MAUI profile text",
            routed_areas={"areas": ["docs"]},
            synthesized_handoff="Thinking... The",
            coder_plan="Update docs/architecture.md only.",
            relevant_files=["docs/architecture.md"],
            recommended_command_groups={"recommended_command_groups": ["markdown-smoke"]},
            workspace_snapshot={"docs/architecture.md": "abc"},
        )

        self.assertIn("Area-reader synthesis unavailable", prompt)
        self.assertNotIn("Thinking... The", prompt)
        self.assertIn("Update docs/architecture.md only.", prompt)
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
                json.dumps(
                    [
                        {
                            "name": "fail",
                            "manual": False,
                            "commands": [
                                {
                                    "argv": [sys.executable, "-c", "import sys; sys.exit(2)"],
                                    "cwd": ".",
                                    "optional": False,
                                }
                            ],
                        }
                    ]
                ),
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
        self.assertEqual(captured["coder"].model, "coder")

    def test_default_provider_configs_include_ollama_commands(self):
        args = run_real_issue.parse_args(
            [
                "--repo",
                ".",
                "--github-repo",
                "owner/repo",
                "--issue",
                "18",
                "--out",
                "out",
            ]
        )

        reader, coder = run_real_issue.resolve_provider_configs(args)

        self.assertEqual(reader.model, "qwen35-9b-32k")
        self.assertEqual(reader.command, "ollama run qwen35-9b-32k")
        self.assertEqual(coder.model, "devstral-small2-12k")
        self.assertEqual(coder.command, "ollama run devstral-small2-12k")

    def test_legacy_model_args_update_generated_ollama_commands(self):
        args = run_real_issue.parse_args(
            [
                "--repo",
                ".",
                "--github-repo",
                "owner/repo",
                "--issue",
                "18",
                "--out",
                "out",
                "--reader",
                "reader-custom",
                "--coder",
                "coder-custom",
            ]
        )

        reader, coder = run_real_issue.resolve_provider_configs(args)

        self.assertEqual(reader.model, "reader-custom")
        self.assertEqual(reader.command, "ollama run reader-custom")
        self.assertEqual(coder.model, "coder-custom")
        self.assertEqual(coder.command, "ollama run coder-custom")

    def test_explicit_command_overrides_generated_ollama_command(self):
        args = run_real_issue.parse_args(
            [
                "--repo",
                ".",
                "--github-repo",
                "owner/repo",
                "--issue",
                "18",
                "--out",
                "out",
                "--reader-model",
                "reader-custom",
                "--reader-command",
                "reader-cli --model reader-custom",
                "--coder-model",
                "coder-custom",
                "--coder-command",
                "coder-cli --model coder-custom",
            ]
        )

        reader, coder = run_real_issue.resolve_provider_configs(args)

        self.assertEqual(reader.model, "reader-custom")
        self.assertEqual(reader.command, "reader-cli --model reader-custom")
        self.assertEqual(coder.model, "coder-custom")
        self.assertEqual(coder.command, "coder-cli --model coder-custom")

    def test_linux_wrapper_delegates_to_script_workflow(self):
        repo_root = Path(__file__).resolve().parents[1]
        root_wrapper = (repo_root / "scripts" / "run-real-issue.sh").read_text(encoding="utf-8")
        linux_cycle = (repo_root / "linux" / "scripts" / "issue-to-pr-cycle.sh").read_text(encoding="utf-8")

        self.assertIn("linux/scripts/issue-to-pr-cycle.sh", root_wrapper)
        self.assertIn("Run one trusted issue-to-PR workflow without a hard-coded agent backend", linux_cycle)
        self.assertIn("issue-to-pr-cycle.sh --env ENV_FILE [--mode Run]", linux_cycle)
        self.assertIn("Plan                       Prepare one issue and write plan.md with the planner agent.", linux_cycle)
        self.assertIn("--planner-agent-command CMD", linux_cycle)
        self.assertIn("--planner-provider NAME", linux_cycle)
        self.assertIn("--planner-model MODEL", linux_cycle)
        self.assertIn("--agent-provider NAME", linux_cycle)
        self.assertIn("--agent-model MODEL", linux_cycle)

    def test_windows_planner_helper_documents_planner_agent_command(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "windows" / "scripts" / "codex-plan-current-issue.ps1"

        text = script.read_text(encoding="utf-8")

        self.assertIn("PlannerAgentCommand", text)
        self.assertIn("{prompt_file}", text)
        self.assertIn("plan.md", text)
        self.assertIn("pwsh -NoProfile -Command", text)
        self.assertNotIn("bash -lc", text)

    def test_script_workflows_use_autodev_labels_and_description_input(self):
        repo_root = Path(__file__).resolve().parents[1]
        linux_prepare = (repo_root / "linux" / "scripts" / "prepare-next-ready-issue.sh").read_text(encoding="utf-8")
        linux_cycle = (repo_root / "linux" / "scripts" / "issue-to-pr-cycle.sh").read_text(encoding="utf-8")
        windows_prepare = (repo_root / "windows" / "scripts" / "codex-prepare-next-ready-issue.ps1").read_text(encoding="utf-8")
        windows_cycle = (repo_root / "windows" / "scripts" / "issue-to-pr-cycle.ps1").read_text(encoding="utf-8")

        self.assertIn("autodev:ready", linux_prepare)
        self.assertIn("autodev:running", linux_prepare)
        self.assertIn("--description", linux_cycle)
        self.assertIn("ISSUE_DESCRIPTION", linux_cycle)
        self.assertNotIn("codex:ready", linux_prepare)
        self.assertNotIn("codex:in-progress", linux_prepare)

        self.assertIn("autodev:ready", windows_prepare)
        self.assertIn("autodev:running", windows_prepare)
        self.assertIn("[string]$Description", windows_prepare)
        self.assertIn("PlannerAgentCommand", windows_cycle)
        self.assertIn("AgentCommand", windows_cycle)

    def test_windows_root_wrapper_delegates_to_native_workflow(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "run-real-issue.ps1"

        text = script.read_text(encoding="utf-8")

        self.assertIn("windows/scripts/issue-to-pr-cycle.ps1", text)
        self.assertNotIn("linux/scripts/issue-to-pr-cycle.sh", text)
        self.assertNotIn("Get-Command bash", text)

    def test_linux_run_once_resolves_workflow_script_relative_to_itself(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "linux" / "run-once.sh"

        text = script.read_text(encoding="utf-8")

        self.assertIn("RUN_ONCE_DIR=", text)
        self.assertIn("$RUN_ONCE_DIR/scripts/issue-to-pr-cycle.sh", text)
        self.assertIn("$RUN_ONCE_DIR/linux/scripts/issue-to-pr-cycle.sh", text)


if __name__ == "__main__":
    unittest.main()

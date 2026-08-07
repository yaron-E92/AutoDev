import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import opencode_adapter


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


class OpenCodeIntegrationTests(unittest.TestCase):
    def test_public_role_commands_are_isolated_thin_and_model_free(self):
        expected_agents = {
            "autodev-read.md": "autodev-reader",
            "autodev-plan.md": "autodev-planner",
            "autodev-implement.md": "autodev-implementer",
            "autodev-fix.md": "autodev-fixer",
            "autodev-verify.md": "autodev-verifier",
        }

        for name, agent in expected_agents.items():
            text = (OPEN_CODE_ROOT / "commands" / name).read_text(encoding="utf-8")
            self.assertIn("$ARGUMENTS", text)
            self.assertIn("subtask: true", text)
            self.assertIn(f"agent: {agent}", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())
            self.assertNotIn("You are the Planner for this repository", text)
            self.assertNotIn("BEGIN_UNIFIED_DIFF", text)

    def test_coordinator_command_is_primary_thin_and_argument_driven(self):
        text = (OPEN_CODE_ROOT / "commands" / "autodev-issue-to-pr.md").read_text(encoding="utf-8")

        self.assertIn("$ARGUMENTS", text)
        self.assertIn("agent: autodev-coordinator", text)
        self.assertIn("subtask: false", text)
        self.assertNotIn("model:", text)
        self.assertNotIn("api_key", text.casefold())
        self.assertNotIn("You are the Planner for this repository", text)
        self.assertNotIn("BEGIN_UNIFIED_DIFF", text)

    def test_role_agents_are_subagents_model_free_and_cannot_spawn_children(self):
        files = sorted(path.name for path in (OPEN_CODE_ROOT / "agents").glob("autodev-*.md"))
        self.assertEqual(files, sorted(opencode_adapter.AGENT_FILES))

        for name in opencode_adapter.AGENT_FILES:
            if name == "autodev-coordinator.md":
                continue
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn("mode: subagent", text)
            self.assertIn("task: deny", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())

        implementer = (OPEN_CODE_ROOT / "agents" / "autodev-implementer.md").read_text(encoding="utf-8")
        fixer = (OPEN_CODE_ROOT / "agents" / "autodev-fixer.md").read_text(encoding="utf-8")
        for text in (implementer, fixer):
            self.assertIn('"git commit*": deny', text)
            self.assertIn('"git push*": deny', text)
            self.assertIn('"gh pr*": deny', text)
            self.assertIn('"gh issue edit*": deny', text)
            self.assertIn('"*.env": deny', text)

        for name in ("autodev-reader.md", "autodev-planner.md", "autodev-verifier.md"):
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn('"*": deny', text)
            self.assertIn('"*.env": deny', text)

    def test_coordinator_task_permission_is_allowlisted_and_edit_is_denied(self):
        text = (OPEN_CODE_ROOT / "agents" / "autodev-coordinator.md").read_text(encoding="utf-8")

        self.assertIn("mode: primary", text)
        self.assertIn("edit: deny", text)
        self.assertIn('"*": deny', text)
        for role in (
            "autodev-reader",
            "autodev-synthesizer",
            "autodev-planner",
            "autodev-implementer",
            "autodev-fixer",
            "autodev-verifier",
        ):
            self.assertIn(f'"{role}": allow', text)
        self.assertNotIn("model:", text)
        self.assertNotIn("api_key", text.casefold())
        self.assertIn("Never merge", text)
        self.assertNotIn("git push", text)

    def test_coordinator_contract_orders_isolated_happy_path_and_repairs(self):
        text = (OPEN_CODE_ROOT / "agents" / "autodev-coordinator.md").read_text(encoding="utf-8")
        body = text.split("---", 2)[-1]

        positions = [
            body.index("1. Preflight and prepare"),
            body.index("2. Reader"),
            body.index("3. Synthesizer"),
            body.index("4. Planner"),
            body.index("5. Implementer"),
            body.index("6. Deterministic verification"),
            body.index("7. Semantic verification"),
            body.index("8. Commit, PR, CI, and CI repair"),
            body.index("9. Ready for human review"),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("localRepairAttempt", body)
        self.assertIn("semanticRepairAttempt", body)
        self.assertIn("ciRepairAttempt", body)
        self.assertIn("stage --name failed", body)
        self.assertIn("stage --name blocked", body)
        self.assertNotIn("-Mode Run", body)

    def test_install_is_idempotent_and_includes_coordinator_without_touching_user_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            custom = target / ".opencode" / "commands" / "custom.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("user-owned\n", encoding="utf-8")

            first = opencode_adapter.install_assets(target, REPO_ROOT, python_command="python-custom")
            second = opencode_adapter.install_assets(target, REPO_ROOT, python_command="python-custom")
            config = json.loads((target / ".opencode" / "autodev.json").read_text(encoding="utf-8"))

            self.assertEqual(len(first), len(second))
            self.assertEqual(custom.read_text(encoding="utf-8"), "user-owned\n")
            self.assertEqual(config["autodev_root"], str(REPO_ROOT.resolve()))
            self.assertEqual(config["python"], "python-custom")
            self.assertNotIn("api_key", json.dumps(config).casefold())
            self.assertTrue((target / ".opencode" / "commands" / "autodev-issue-to-pr.md").is_file())
            self.assertTrue((target / ".opencode" / "agents" / "autodev-coordinator.md").is_file())

    def test_missing_current_issue_delegates_only_to_existing_prepare_stage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            commands = []

            def fake_runner(command, **kwargs):
                commands.append(command)
                current = repo / ".codex-run" / "current"
                current.mkdir(parents=True)
                (current / "state.json").write_text(
                    json.dumps({"IssueNumber": 62}),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="PREPARED", stderr="")

            current = opencode_adapter.ensure_current_issue(
                repo,
                REPO_ROOT,
                "62",
                runner=fake_runner,
            )

        self.assertEqual(current.name, "current")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("issue-to-pr-cycle.ps1", " ".join(command))
        self.assertIn("Prepare", command)
        self.assertNotIn("Run", command)
        self.assertNotIn("-ProviderProfile", command)

    def test_existing_current_issue_prevents_duplicate_prepare_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo, IssueNumber=62)

            def unexpected_runner(*args, **kwargs):
                self.fail("Prepare should not run again for the already-current issue")

            actual = opencode_adapter.ensure_current_issue(repo, REPO_ROOT, "62", runner=unexpected_runner)

            self.assertTrue(actual.samefile(current))

    def test_planner_prepare_uses_bounded_artifacts_and_existing_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            (current / "state.json").write_text(
                json.dumps(
                    {
                        "IssueNumber": 62,
                        "IssueText": "# GitHub Issue #62: OpenCode coordinator",
                        "LocalCheck": "local-check",
                        "StackContext": "Python automation",
                        "Labels": ["area:python"],
                        "ProviderProfile": "",
                    }
                ),
                encoding="utf-8",
            )
            (current / "issue.md").write_text("# GitHub Issue #62: OpenCode coordinator\n", encoding="utf-8")
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            (current / "routed-areas.json").write_text('{"areas": ["python"]}\n', encoding="utf-8")
            (current / "synthesized-handoff.md").write_text(
                "Bounded handoff with enough repository evidence to remain valid for planner prompt rendering.\n",
                encoding="utf-8",
            )
            (current / "coder-plan.md").write_text("Reader plan\n", encoding="utf-8")
            (current / "recommended-command-groups.json").write_text("{}\n", encoding="utf-8")

            path = opencode_adapter.prepare_role("planner", repo, "62", autodev_root=REPO_ROOT)
            prompt = path.read_text(encoding="utf-8")

        self.assertEqual(path.name, "planner.md")
        self.assertIn("Bounded handoff", prompt)
        self.assertIn("Role-specific prompt policy (lite; autodev-ponytail-v1)", prompt)
        self.assertIn("# GitHub Issue #62", prompt)

    def test_accept_planner_reuses_existing_six_section_parser(self):
        plan = """1) Where to look
- automation/opencode_adapter.py
2) Files / areas likely to touch
- automation/opencode_adapter.py
3) Assumptions
- None
4) Plan
- Implement the thin bridge.
5) Risks / gotchas
- Keep workflow ownership unchanged.
6) Recommended implementation approach
- Option A: thin role frontend.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            plan_path = current / "plan.md"
            plan_path.write_text(plan, encoding="utf-8")

            outputs = opencode_adapter.accept_role("planner", repo, plan_path)

            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0].samefile(plan_path))
            self.assertTrue(plan_path.read_text(encoding="utf-8").startswith("1) Where to look"))

    def test_accept_verifier_reuses_semantic_schema_and_artifacts(self):
        result = {
            "verdict": "pass",
            "requirements": [
                {"criterion": "OpenCode role frontend exists", "status": "met", "evidence": ["integration"]}
            ],
            "findings": [],
            "repair_brief": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            result_path = current / "verification-result.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")

            outputs = opencode_adapter.accept_role("verifier", repo, result_path)
            attempt_path = current / "verification" / "semantic-attempt-0.json"

            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["verdict"], "pass")
            self.assertTrue(any(path.samefile(attempt_path) for path in outputs))
            self.assertTrue((current / "verification" / "final-verdict.json").is_file())

    def test_semantic_repair_then_pass_preserves_both_attempts(self):
        repair = {
            "verdict": "repair",
            "requirements": [{"criterion": "criterion", "status": "missing", "evidence": ["diff"]}],
            "findings": [{"severity": "blocking", "message": "repair", "path": "file.py"}],
            "repair_brief": "Fix the criterion.",
        }
        passed = {
            "verdict": "pass",
            "requirements": [{"criterion": "criterion", "status": "met", "evidence": ["diff"]}],
            "findings": [],
            "repair_brief": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            result_path = current / "verification-result.json"

            result_path.write_text(json.dumps(repair), encoding="utf-8")
            opencode_adapter.accept_role("verifier", repo, result_path)
            result_path.write_text(json.dumps(passed), encoding="utf-8")
            opencode_adapter.accept_role("verifier", repo, result_path)

            self.assertTrue((current / "verification" / "semantic-attempt-0.json").is_file())
            self.assertTrue((current / "verification" / "semantic-attempt-1.json").is_file())
            self.assertTrue((current / "verification" / "final-verdict.json").is_file())

    def test_accept_verifier_repair_does_not_write_final_verdict(self):
        result = {
            "verdict": "repair",
            "requirements": [
                {"criterion": "OpenCode role frontend exists", "status": "missing", "evidence": ["integration"]}
            ],
            "findings": [
                {"severity": "blocking", "message": "Repair is required.", "path": "automation/opencode_adapter.py"}
            ],
            "repair_brief": "Fix the missing behavior.",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            verification = current / "verification"
            verification.mkdir(parents=True)
            final_path = verification / "final-verdict.json"
            final_path.write_text('{"verdict":"pass"}\n', encoding="utf-8")
            result_path = current / "verification-result.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")

            outputs = opencode_adapter.accept_role("verifier", repo, result_path)
            attempt_path = verification / "semantic-attempt-0.json"

            self.assertTrue(any(path.samefile(attempt_path) for path in outputs))
            self.assertFalse(any(path.name == "final-verdict.json" for path in outputs))
            self.assertFalse(final_path.exists())

    def test_coordinator_local_check_maps_pass_repair_and_exhaustion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(repo, IssueNumber=62, Status="ImplementerPromptRendered")
            responses = [
                SimpleNamespace(returncode=10, stdout="LOCAL_CHECK_FAILED", stderr=""),
                SimpleNamespace(returncode=0, stdout="LOCAL_CHECK_PASSED", stderr=""),
                SimpleNamespace(returncode=10, stdout="LOCAL_CHECK_FAILED", stderr=""),
            ]

            def fake_runner(*args, **kwargs):
                return responses.pop(0)

            with patch.object(opencode_adapter, "_repository_modified", return_value=True):
                _, repair = opencode_adapter.workflow_stage("local-check", repo, autodev_root=REPO_ROOT, attempt=0, runner=fake_runner)
                _, passed = opencode_adapter.workflow_stage("local-check", repo, autodev_root=REPO_ROOT, attempt=1, runner=fake_runner)
                _, exhausted = opencode_adapter.workflow_stage("local-check", repo, autodev_root=REPO_ROOT, attempt=3, runner=fake_runner)

            self.assertEqual(repair["state"], "REPAIR")
            self.assertTrue(str(repair["artifact"]).endswith("local-repair.md"))
            self.assertEqual(passed["state"], "CONTINUE")
            self.assertEqual(exhausted["state"], "BLOCKED")

    def test_coordinator_semantic_repair_and_blocked_paths_do_not_advance_pr(self):
        repair = {
            "verdict": "repair",
            "requirements": [{"criterion": "criterion", "status": "missing", "evidence": ["diff"]}],
            "findings": [{"severity": "blocking", "message": "repair", "path": "file.py"}],
            "repair_brief": "Fix it.",
        }
        blocked = {
            "verdict": "blocked",
            "requirements": [{"criterion": "criterion", "status": "uncertain", "evidence": ["missing evidence"]}],
            "findings": [{"severity": "blocking", "message": "blocked", "path": "file.py"}],
            "repair_brief": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo, IssueNumber=62, Status="LocalCheckPassed", PrUrl="", PrNumber=0)
            result_path = current / "verification-result.json"
            result_path.write_text(json.dumps(repair), encoding="utf-8")

            def fake_prepare(repo_arg, current_arg, template_arg, output_arg):
                Path(output_arg).write_text("semantic repair prompt\n", encoding="utf-8")

            with patch.object(opencode_adapter, "prepare_semantic_repair_prompt", side_effect=fake_prepare), patch.object(
                opencode_adapter, "_repository_modified", return_value=True
            ):
                _, repair_result = opencode_adapter.workflow_stage("semantic", repo, autodev_root=REPO_ROOT, attempt=0)
                result_path.write_text(json.dumps(blocked), encoding="utf-8")
                _, blocked_result = opencode_adapter.workflow_stage("semantic", repo, autodev_root=REPO_ROOT, attempt=0)

            self.assertEqual(repair_result["state"], "REPAIR")
            self.assertTrue(str(repair_result["artifact"]).endswith("verification-repair.md"))
            self.assertEqual(blocked_result["state"], "BLOCKED")
            self.assertFalse(blocked_result["pr_exists"])

    def test_coordinator_ci_repair_reuses_pr_boundary_and_exhausts_safely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(
                repo,
                IssueNumber=62,
                Status="CiFailed",
                LastCommitSha="abc123",
                PrUrl="https://github.com/yaron-E92/AutoDev/pull/999",
                PrNumber=999,
            )
            responses = [
                SimpleNamespace(returncode=20, stdout="CI_FAILED", stderr=""),
                SimpleNamespace(returncode=0, stdout="CI_PASSED", stderr=""),
                SimpleNamespace(returncode=20, stdout="CI_FAILED", stderr=""),
            ]

            def fake_runner(*args, **kwargs):
                return responses.pop(0)

            with patch.object(opencode_adapter, "_repository_modified", return_value=True):
                _, repair = opencode_adapter.workflow_stage("pr-and-ci", repo, autodev_root=REPO_ROOT, attempt=0, runner=fake_runner)
                _, passed = opencode_adapter.workflow_stage("pr-and-ci", repo, autodev_root=REPO_ROOT, attempt=1, runner=fake_runner)
                _, exhausted = opencode_adapter.workflow_stage("pr-and-ci", repo, autodev_root=REPO_ROOT, attempt=3, runner=fake_runner)

            self.assertEqual(repair["state"], "REPAIR")
            self.assertTrue(repair["commit_exists"])
            self.assertTrue(repair["pr_exists"])
            self.assertEqual(passed["state"], "CONTINUE")
            self.assertEqual(exhausted["state"], "BLOCKED")

    def test_coordinator_ready_reports_pr_ready_and_never_merges(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(
                repo,
                IssueNumber=62,
                Status="CiPassedVerifierPromptRendered",
                LastCommitSha="abc123",
                PrUrl="https://github.com/yaron-E92/AutoDev/pull/999",
                PrNumber=999,
            )
            commands = []

            def fake_runner(command, **kwargs):
                commands.append(command)
                return SimpleNamespace(returncode=0, stdout="MARKED_READY_FOR_REVIEW", stderr="")

            with patch.object(opencode_adapter, "_repository_modified", return_value=True):
                _, result = opencode_adapter.workflow_stage("ready", repo, autodev_root=REPO_ROOT, runner=fake_runner)

            self.assertEqual(result["state"], "PR_READY")
            self.assertEqual(result["pr_url"], "https://github.com/yaron-E92/AutoDev/pull/999")
            rendered = " ".join(commands[0])
            self.assertIn("ReadyForReview", rendered)
            self.assertNotIn("merge", rendered.casefold())

    def test_coordinator_unexpected_stage_failure_is_actionable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_state(repo, IssueNumber=62, Status="ImplementerPromptRendered")

            def fake_runner(*args, **kwargs):
                return SimpleNamespace(returncode=9, stdout="", stderr="tool failed")

            with patch.object(opencode_adapter, "_repository_modified", return_value=True):
                code, result = opencode_adapter.workflow_stage("local-check", repo, autodev_root=REPO_ROOT, runner=fake_runner)

            self.assertEqual(code, 1)
            self.assertEqual(result["state"], "FAILED")
            self.assertEqual(result["issue_number"], 62)
            self.assertEqual(result["failed_stage"], "local-check")
            self.assertIn("tool failed", result["reason"])
            self.assertTrue(result["repository_modified"])
            self.assertTrue(result["next_action"])

    def test_reader_handoff_rejects_unbounded_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            result = current / "reader-brief.md"
            result.write_text("x" * (opencode_adapter.MAX_HANDOFF_CHARS + 1), encoding="utf-8")

            with self.assertRaises(opencode_adapter.OpenCodeAdapterError):
                opencode_adapter.accept_role("reader", repo, result)

    def test_existing_workflow_entrypoints_do_not_depend_on_opencode_adapter(self):
        paths = (
            REPO_ROOT / "scripts" / "run-real-issue.ps1",
            REPO_ROOT / "windows" / "scripts" / "issue-to-pr-cycle.ps1",
            REPO_ROOT / "automation" / "prompt_runner.py",
            REPO_ROOT / "automation" / "run_real_issue.py",
        )

        for path in paths:
            self.assertNotIn("opencode_adapter", path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_state(repo: Path, **overrides) -> Path:
        current = repo / ".codex-run" / "current"
        current.mkdir(parents=True, exist_ok=True)
        state = {
            "IssueNumber": 62,
            "IssueText": "# GitHub Issue #62: coordinator",
            "BranchName": "autodev/issue-62",
            "Status": "Prepared",
            "LastCommitSha": "",
            "PrUrl": "",
            "PrNumber": 0,
        }
        state.update(overrides)
        (current / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return current


if __name__ == "__main__":
    unittest.main()

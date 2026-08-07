import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import opencode_adapter


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


class OpenCodeIntegrationTests(unittest.TestCase):
    def test_public_role_commands_are_isolated_portable_and_model_free(self):
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
            self.assertIn(".opencode/autodev.py", text)
            self.assertNotIn("autodev.ps1", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())

    def test_coordinator_is_primary_portable_and_task_allowlisted(self):
        command = (OPEN_CODE_ROOT / "commands" / "autodev-issue-to-pr.md").read_text(encoding="utf-8")
        agent = (OPEN_CODE_ROOT / "agents" / "autodev-coordinator.md").read_text(encoding="utf-8")

        self.assertIn("$ARGUMENTS", command)
        self.assertIn("agent: autodev-coordinator", command)
        self.assertIn("subtask: false", command)
        self.assertIn("mode: primary", agent)
        self.assertIn("edit: deny", agent)
        self.assertIn(".opencode/autodev.py", agent)
        self.assertNotIn("autodev.ps1", agent)
        self.assertNotIn("windows/scripts", agent)
        for role in (
            "autodev-reader",
            "autodev-synthesizer",
            "autodev-planner",
            "autodev-implementer",
            "autodev-fixer",
            "autodev-verifier",
        ):
            self.assertIn(f'"{role}": allow', agent)
        self.assertNotIn("model:", agent)
        self.assertNotIn("api_key", agent.casefold())

    def test_role_agents_are_subagents_model_free_and_portable(self):
        files = sorted(path.name for path in (OPEN_CODE_ROOT / "agents").glob("autodev-*.md"))
        self.assertEqual(files, sorted(opencode_adapter.AGENT_FILES))

        for name in opencode_adapter.AGENT_FILES:
            if name == "autodev-coordinator.md":
                continue
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn("mode: subagent", text)
            self.assertIn("task: deny", text)
            self.assertIn('"python .opencode/autodev.py *": allow', text)
            self.assertIn('"python3 .opencode/autodev.py *": allow', text)
            self.assertNotIn("autodev.ps1", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())

    def test_implementer_and_fixer_keep_vcs_mutation_denied(self):
        for name in ("autodev-implementer.md", "autodev-fixer.md"):
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn('"git commit*": deny', text)
            self.assertIn('"git push*": deny', text)
            self.assertIn('"gh pr*": deny', text)
            self.assertIn('"gh issue edit*": deny', text)
            self.assertIn('"*.env": deny', text)

    def test_coordinator_contract_orders_isolated_happy_path_and_repairs(self):
        body = (OPEN_CODE_ROOT / "agents" / "autodev-coordinator.md").read_text(encoding="utf-8").split("---", 2)[-1]
        headings = [
            "1. Preflight and prepare",
            "2. Reader",
            "3. Synthesizer",
            "4. Planner",
            "5. Implementer",
            "6. Deterministic verification",
            "7. Semantic verification",
            "8. Commit, PR, CI, and CI repair",
            "9. Ready for human review",
        ]
        positions = [body.index(value) for value in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("localRepairAttempt", body)
        self.assertIn("semanticRepairAttempt", body)
        self.assertIn("ciRepairAttempt", body)
        self.assertIn("stage --name failed", body)
        self.assertIn("stage --name blocked", body)
        self.assertNotIn("PowerShell workflow", body.split("Do not route", 1)[-1] if "Do not route" in body else "")

    def test_install_is_idempotent_and_includes_portable_bridge(self):
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
            self.assertTrue((target / ".opencode" / "autodev.py").is_file())
            self.assertTrue((target / ".opencode" / "autodev.ps1").is_file())
            self.assertTrue((target / ".opencode" / "commands" / "autodev-issue-to-pr.md").is_file())
            self.assertTrue((target / ".opencode" / "agents" / "autodev-coordinator.md").is_file())
            self.assertNotIn("api_key", json.dumps(config).casefold())

    def test_missing_current_issue_delegates_to_portable_prepare(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            (current / "state.json").write_text(json.dumps({"IssueNumber": 65}), encoding="utf-8")

            with patch("automation.opencode_adapter.workflow_stages.ensure_prepared_issue", return_value=current) as prepare:
                actual = opencode_adapter.ensure_current_issue(repo, REPO_ROOT, "65")

            self.assertTrue(actual.samefile(current))
            prepare.assert_called_once()

    def test_existing_current_issue_is_reused_by_portable_prepare(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo, IssueNumber=65)

            with patch("automation.workflow_stages.gh", side_effect=AssertionError("GitHub mutation should not run")):
                actual = opencode_adapter.ensure_current_issue(repo, REPO_ROOT, "65")

            self.assertTrue(actual.samefile(current))

    def test_planner_prepare_uses_bounded_artifacts_and_existing_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                IssueNumber=65,
                IssueText="# GitHub Issue #65: Cross-platform OpenCode",
                LocalCheck="local-check",
                StackContext="Python automation",
                Labels=["area:python"],
                ProviderProfile="",
            )
            (current / "issue.md").write_text("# GitHub Issue #65: Cross-platform OpenCode\n", encoding="utf-8")
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            (current / "routed-areas.json").write_text('{"areas": ["python"]}\n', encoding="utf-8")
            (current / "synthesized-handoff.md").write_text(
                "Bounded handoff with enough repository evidence to remain valid for planner prompt rendering.\n",
                encoding="utf-8",
            )
            (current / "coder-plan.md").write_text("Reader plan\n", encoding="utf-8")
            (current / "recommended-command-groups.json").write_text("{}\n", encoding="utf-8")

            path = opencode_adapter.prepare_role("planner", repo, "65", autodev_root=REPO_ROOT)
            prompt = path.read_text(encoding="utf-8")

            self.assertIn("Bounded handoff", prompt)
            self.assertIn("Role-specific prompt policy (lite; autodev-ponytail-v1)", prompt)
            self.assertIn("# GitHub Issue #65", prompt)

    def test_accept_planner_reuses_existing_six_section_parser(self):
        plan = """1) Where to look
- automation/workflow_stages.py
2) Files / areas likely to touch
- automation/workflow_stages.py
3) Assumptions
- None
4) Plan
- Implement the portable stage backend.
5) Risks / gotchas
- Keep workflow ownership unchanged.
6) Recommended implementation approach
- Option A: shared Python stages.
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

    def test_semantic_repair_then_pass_preserves_attempt_history(self):
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

    def test_reader_handoff_rejects_unbounded_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            result = current / "reader-brief.md"
            result.write_text("x" * (opencode_adapter.MAX_HANDOFF_CHARS + 1), encoding="utf-8")

            with self.assertRaises(opencode_adapter.OpenCodeAdapterError):
                opencode_adapter.accept_role("reader", repo, result)

    def test_opencode_adapter_has_no_windows_workflow_backend(self):
        adapter = (REPO_ROOT / "automation" / "opencode_adapter.py").read_text(encoding="utf-8")
        portable = (REPO_ROOT / "integrations" / "opencode" / "autodev.py").read_text(encoding="utf-8")

        self.assertNotIn("windows/scripts", adapter)
        self.assertNotIn("issue-to-pr-cycle.ps1", adapter)
        self.assertNotIn("pwsh", portable)
        self.assertIn("automation.opencode_adapter", portable)

    def test_existing_workflow_entrypoints_do_not_depend_on_opencode_adapter(self):
        paths = (
            REPO_ROOT / "scripts" / "run-real-issue.ps1",
            REPO_ROOT / "windows" / "scripts" / "issue-to-pr-cycle.ps1",
            REPO_ROOT / "linux" / "scripts" / "issue-to-pr-cycle.sh",
            REPO_ROOT / "automation" / "prompt_runner.py",
            REPO_ROOT / "automation" / "run_real_issue.py",
        )
        for path in paths:
            self.assertNotIn("opencode_adapter", path.read_text(encoding="utf-8"))

    def _write_state(self, repo: Path, **overrides):
        current = repo / ".codex-run" / "current"
        current.mkdir(parents=True, exist_ok=True)
        state = {
            "IssueNumber": 65,
            "Status": "Prepared",
            "BranchName": "autodev/issue-65",
            "LastCommitSha": "",
            "PrUrl": "",
            "ProviderProfile": "",
        }
        state.update(overrides)
        (current / "state.json").write_text(json.dumps(state), encoding="utf-8")
        return current


if __name__ == "__main__":
    unittest.main()

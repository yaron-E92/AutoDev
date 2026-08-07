import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import opencode_adapter


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


class OpenCodeIntegrationTests(unittest.TestCase):
    def test_public_commands_are_isolated_thin_and_model_free(self):
        expected_agents = {
            "autodev-read.md": "autodev-reader",
            "autodev-plan.md": "autodev-planner",
            "autodev-implement.md": "autodev-implementer",
            "autodev-fix.md": "autodev-fixer",
            "autodev-verify.md": "autodev-verifier",
        }
        files = sorted(path.name for path in (OPEN_CODE_ROOT / "commands").glob("autodev-*.md"))

        self.assertEqual(files, sorted(expected_agents))
        for name, agent in expected_agents.items():
            text = (OPEN_CODE_ROOT / "commands" / name).read_text(encoding="utf-8")
            self.assertIn("$ARGUMENTS", text)
            self.assertIn("subtask: true", text)
            self.assertIn(f"agent: {agent}", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())
            self.assertNotIn("You are the Planner for this repository", text)
            self.assertNotIn("BEGIN_UNIFIED_DIFF", text)

    def test_agents_are_subagents_model_free_and_cannot_spawn_children(self):
        files = sorted(path.name for path in (OPEN_CODE_ROOT / "agents").glob("autodev-*.md"))
        self.assertEqual(files, sorted(opencode_adapter.AGENT_FILES))

        for name in files:
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

    def test_install_is_idempotent_and_preserves_non_autodev_files(self):
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
            self.assertTrue((target / ".opencode" / "commands" / "autodev-plan.md").is_file())
            self.assertTrue((target / ".opencode" / "agents" / "autodev-verifier.md").is_file())

    def test_missing_current_issue_delegates_only_to_existing_prepare_stage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            commands = []

            def fake_runner(command, **kwargs):
                commands.append(command)
                current = repo / ".codex-run" / "current"
                current.mkdir(parents=True)
                (current / "state.json").write_text(
                    json.dumps({"IssueNumber": 49}),
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0, stdout="PREPARED", stderr="")

            current = opencode_adapter.ensure_current_issue(
                repo,
                REPO_ROOT,
                "49",
                runner=fake_runner,
            )

        self.assertEqual(current.name, "current")
        self.assertEqual(len(commands), 1)
        command = commands[0]
        self.assertIn("issue-to-pr-cycle.ps1", " ".join(command))
        self.assertIn("Prepare", command)
        self.assertNotIn("Run", command)
        self.assertNotIn("-ProviderProfile", command)

    def test_planner_prepare_uses_bounded_artifacts_and_existing_policy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".codex-run" / "current"
            current.mkdir(parents=True)
            (current / "state.json").write_text(
                json.dumps(
                    {
                        "IssueNumber": 49,
                        "IssueText": "# GitHub Issue #49: OpenCode",
                        "LocalCheck": "local-check",
                        "StackContext": "Python automation",
                        "Labels": ["area:python"],
                        "ProviderProfile": "",
                    }
                ),
                encoding="utf-8",
            )
            (current / "issue.md").write_text("# GitHub Issue #49: OpenCode\n", encoding="utf-8")
            (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
            (current / "routed-areas.json").write_text('{"areas": ["python"]}\n', encoding="utf-8")
            (current / "synthesized-handoff.md").write_text("Bounded handoff\n", encoding="utf-8")
            (current / "coder-plan.md").write_text("Reader plan\n", encoding="utf-8")
            (current / "recommended-command-groups.json").write_text("{}\n", encoding="utf-8")

            path = opencode_adapter.prepare_role("planner", repo, "49", autodev_root=REPO_ROOT)
            prompt = path.read_text(encoding="utf-8")

        self.assertEqual(path.name, "planner.md")
        self.assertIn("Bounded handoff", prompt)
        self.assertIn("Role-specific prompt policy (lite; autodev-ponytail-v1)", prompt)
        self.assertIn("# GitHub Issue #49", prompt)

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

            self.assertEqual(outputs, [plan_path])
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

            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["verdict"], "pass")
            self.assertIn(current / "verification" / "semantic-attempt-0.json", outputs)
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

            self.assertIn(verification / "semantic-attempt-0.json", outputs)
            self.assertNotIn(final_path, outputs)
            self.assertFalse(final_path.exists())

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


if __name__ == "__main__":
    unittest.main()

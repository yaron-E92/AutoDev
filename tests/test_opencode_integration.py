import hashlib
import json
import re
import shlex
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
            self.assertNotIn("portable bridge `accept", text)

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
        self.assertIn("non-retryable-deterministic", agent)
        self.assertIn("repeated-failure fingerprint", agent)
        self.assertNotIn("prepare --role implementer", agent)
        self.assertNotIn("autodev.py ...", agent)

    def test_role_agents_are_subagents_model_free_and_use_exact_bridge_permissions(self):
        files = sorted(path.name for path in (OPEN_CODE_ROOT / "agents").glob("autodev-*.md"))
        self.assertEqual(files, sorted(opencode_adapter.AGENT_FILES))

        for name in opencode_adapter.AGENT_FILES:
            if name == "autodev-coordinator.md":
                continue
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn("mode: subagent", text)
            self.assertIn("task: deny", text)
            self.assertIn(".opencode/autodev.py", text)
            self.assertNotIn('"python .opencode/autodev.py *": allow', text)
            self.assertNotIn('"python3 .opencode/autodev.py *": allow', text)
            self.assertNotIn("autodev.ps1", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())

    def test_reader_planner_and_verifier_remain_non_source_editing(self):
        for name in ("autodev-reader.md", "autodev-planner.md", "autodev-verifier.md"):
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn("edit:\n    \"*\": deny", text)

    def test_editing_and_verifying_roles_allow_routine_commands_but_deny_vcs_mutation(self):
        for name in ("autodev-implementer.md", "autodev-fixer.md", "autodev-verifier.md"):
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn('"git commit*": deny', text)
            self.assertIn('"git push*": deny', text)
            self.assertIn('"gh pr*": deny', text)
            self.assertIn('"gh issue edit*": deny', text)
            self.assertIn('"git status*": allow', text)
            self.assertIn('"git diff*": allow', text)
            self.assertIn('"dotnet build*": allow', text)
            self.assertIn('"dotnet test*": allow', text)
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
        self.assertIn("accept --role verifier --input .codex-run/current/verification-result.json", body)
        self.assertIn("do not run prepare", body.casefold())

    def test_checked_in_bridge_snippets_use_only_real_argparse_commands(self):
        parser = opencode_adapter.build_parser()
        paths = list((OPEN_CODE_ROOT / "agents").glob("autodev-*.md")) + list(
            (OPEN_CODE_ROOT / "commands").glob("autodev-*.md")
        )
        pattern = re.compile(r"`(python3? \.opencode/autodev\.py [^`]+)`")
        seen = 0
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for snippet in pattern.findall(text):
                seen += 1
                tokens = shlex.split(snippet)[2:]
                self.assertIn(tokens[0], {"prepare", "accept", "stage"}, f"{path}: {snippet}")
                normalized = []
                for index, token in enumerate(tokens):
                    if token.startswith("<") and token.endswith(">"):
                        previous = tokens[index - 1] if index else ""
                        token = "0" if previous == "--attempt" else "value"
                    normalized.append(token)
                parser.parse_args(normalized)
        self.assertGreater(seen, 10)

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

    def test_planner_prepare_writes_contract_and_parser_template(self):
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
            template = (current / "plan.template.md").read_text(encoding="utf-8")
            contracts = json.loads((current / "role-contracts.json").read_text(encoding="utf-8"))

            self.assertIn("Bounded handoff", prompt)
            self.assertIn("Role-specific prompt policy (lite; autodev-ponytail-v1)", prompt)
            self.assertIn("# GitHub Issue #65", prompt)
            for heading in opencode_adapter.REQUIRED_PLAN_HEADINGS:
                self.assertIn(heading, template)
            self.assertEqual(contracts["protocol_correction_limit"], 1)
            self.assertEqual(set(contracts["roles"]), set(opencode_adapter.ROLE_NAMES))

    def test_verifier_prepare_prepopulates_exact_acceptance_criteria(self):
        issue_text = "# Issue\n\n## Acceptance criteria\n- First exact criterion\n- Second exact criterion\n"
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(
                repo,
                IssueNumber=67,
                IssueText=issue_text,
                LocalCheck="check",
                StackContext="Python",
            )
            (current / "issue.md").write_text(issue_text, encoding="utf-8")
            (current / "plan.md").write_text("Plan\n", encoding="utf-8")
            with (
                patch("automation.opencode_adapter.collect_changed_files", return_value=[]),
                patch("automation.opencode_adapter.collect_current_diff", return_value=""),
                patch("automation.opencode_adapter.collect_deterministic_evidence", return_value="passed"),
            ):
                opencode_adapter.prepare_role("verifier", repo, "67", autodev_root=REPO_ROOT)

            template = json.loads((current / "verification-result.template.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [item["criterion"] for item in template["requirements"]],
                ["First exact criterion", "Second exact criterion"],
            )
            self.assertEqual(template["findings"], [])

    def test_semantic_template_matches_parser_and_structural_errors_are_aggregated(self):
        template = opencode_adapter.semantic_result_template(["Exact criterion"])
        parsed = opencode_adapter.parse_semantic_output(
            json.dumps(template),
            expected_criteria=["Exact criterion"],
        )
        self.assertEqual(parsed["verdict"], "blocked")
        self.assertEqual(parsed["requirements"][0]["criterion"], "Exact criterion")

        malformed = {
            "verdict": "APPROVED",
            "requirements": "not-an-array",
            "findings": [{"severity": "info", "message": "", "path": 7}],
            "repair_brief": [],
        }
        with self.assertRaises(opencode_adapter.SemanticVerifierError) as raised:
            opencode_adapter.parse_semantic_output(json.dumps(malformed))
        message = str(raised.exception)
        self.assertIn("verdict must be", message)
        self.assertIn("requirements must be an array", message)
        self.assertIn("severity must be blocking or warning", message)
        self.assertIn("message must be non-empty text", message)
        self.assertIn("path must be text", message)
        self.assertIn("repair_brief must be text", message)

    def test_accept_planner_reuses_existing_six_section_parser_and_pins_artifact(self):
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
            current = self._write_state(repo)
            plan_path = current / "plan.md"
            plan_path.write_text(plan, encoding="utf-8")

            outputs = opencode_adapter.accept_role("planner", repo, plan_path)
            state = json.loads((current / "state.json").read_text(encoding="utf-8"))

            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0].samefile(plan_path))
            accepted = state["AcceptedRoleArtifacts"]["planner"]
            self.assertEqual(
                accepted["sha256"],
                hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            )

    def test_protocol_rejection_allows_exactly_one_correction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._write_state(repo)
            plan = current / "plan.md"
            plan.write_text("not a valid six-section plan\n", encoding="utf-8")
            (current / "plan.template.md").write_text(
                "\n\n".join(opencode_adapter.REQUIRED_PLAN_HEADINGS) + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(opencode_adapter.OpenCodeAdapterError) as first:
                opencode_adapter.accept_role("planner", repo, plan)
            self.assertIn("one correction is allowed", str(first.exception))
            self.assertTrue((current / "contract-correction-planner.md").is_file())

            with self.assertRaises(opencode_adapter.OpenCodeAdapterError) as second:
                opencode_adapter.accept_role("planner", repo, plan)
            self.assertIn("correction limit exhausted", str(second.exception))
            diagnostics = json.loads((current / "run-diagnostics.json").read_text(encoding="utf-8"))
            self.assertEqual(diagnostics["protocol_correction_attempts"]["planner"], 1)

    def test_role_contract_bridge_snippets_match_real_argparse_surface(self):
        parser = opencode_adapter.build_parser()
        for contract in opencode_adapter.role_contracts().values():
            accept = str(contract["accept"])
            parser.parse_args(shlex.split(accept)[2:])
            prepare = contract["prepare"]
            prepare_commands = prepare if isinstance(prepare, list) else [prepare]
            for command in prepare_commands:
                parser.parse_args(shlex.split(str(command))[2:])

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
            current = self._write_state(repo)
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
            current = self._write_state(repo)
            result = current / "reader-brief.md"
            result.write_text("x" * (opencode_adapter.MAX_HANDOFF_CHARS + 1), encoding="utf-8")

            with self.assertRaises(opencode_adapter.OpenCodeAdapterError):
                opencode_adapter.accept_role("reader", repo, result)
            self.assertTrue((current / "contract-correction-reader.md").is_file())

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

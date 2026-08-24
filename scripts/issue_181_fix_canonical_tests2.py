from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_method(text: str, name: str, body: str) -> str:
    pattern = re.compile(
        rf"\n    def {re.escape(name)}\(self.*?(?=\n    def |\n\nif __name__ ==)",
        re.S,
    )
    replacement = "\n" + body.rstrip() + "\n"
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"test method not found: {name}")
    return text


def fix_autodev_cli() -> None:
    path = ROOT / "tests" / "test_autodev_cli.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('self.assertNotIn("autodev", text)', 'self.assertNotIn("python3 .opencode/autodev.py", text)')
    path.write_text(text, encoding="utf-8")


def fix_integration() -> None:
    path = ROOT / "tests" / "test_opencode_integration.py"
    text = path.read_text(encoding="utf-8")

    text = replace_method(text, "test_public_role_commands_are_isolated_portable_and_model_free", '''    def test_public_role_commands_are_isolated_portable_and_model_free(self):
        expected_roles = {
            "autodev-read.md": "reader",
            "autodev-plan.md": "planner",
            "autodev-implement.md": "implementer",
            "autodev-fix.md": "fixer",
            "autodev-verify.md": "verifier",
        }
        for name, role in expected_roles.items():
            text = (OPEN_CODE_ROOT / "commands" / name).read_text(encoding="utf-8")
            self.assertIn("$ARGUMENTS", text)
            self.assertIn("subtask: false", text)
            self.assertIn("agent: build", text)
            self.assertIn(f"!`autodev role --role {role}", text)
            self.assertIn("--interactive-consent", text)
            self.assertNotIn(".opencode/autodev", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())''')

    text = replace_method(text, "test_coordinator_is_primary_portable_and_task_allowlisted", '''    def test_coordinator_is_primary_portable_and_task_allowlisted(self):
        command = (OPEN_CODE_ROOT / "commands" / "autodev-issue-to-pr.md").read_text(encoding="utf-8")
        agent = (OPEN_CODE_ROOT / "agents" / "autodev-coordinator.md").read_text(encoding="utf-8")

        self.assertIn("$1", command)
        self.assertIn("agent: build", command)
        self.assertIn("subtask: false", command)
        self.assertIn("!`autodev coordinate", command)
        self.assertIn("--interactive-consent", command)
        self.assertIn("mode: primary", agent)
        self.assertIn("edit: deny", agent)
        self.assertNotIn(".opencode/autodev", agent)
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
        self.assertIn('"autodev stage *": allow', agent)''')

    text = replace_method(text, "test_role_agents_are_subagents_model_free_and_use_exact_bridge_permissions", '''    def test_role_agents_are_subagents_model_free_and_use_exact_bridge_permissions(self):
        files = sorted(path.name for path in (OPEN_CODE_ROOT / "agents").glob("autodev-*.md"))
        self.assertEqual(files, sorted(opencode_adapter_contract.AGENT_FILES))

        for name in opencode_adapter_contract.AGENT_FILES:
            if name == "autodev-coordinator.md":
                continue
            text = (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")
            self.assertIn("mode: all", text)
            self.assertIn("task: deny", text)
            self.assertIn("Canonical AutoDev launcher", text)
            self.assertIn("autodev", text)
            self.assertNotIn(".opencode/autodev", text)
            self.assertNotIn("model:", text)
            self.assertNotIn("api_key", text.casefold())''')

    text = replace_method(text, "test_checked_in_bridge_snippets_use_only_real_argparse_commands", '''    def test_checked_in_bridge_snippets_use_only_real_argparse_commands(self):
        paths = list((OPEN_CODE_ROOT / "commands").glob("autodev-*.md"))
        pattern = re.compile(r"!`(autodev [^`]+)`")
        seen = 0
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(".opencode/autodev", text, path.name)
            self.assertNotIn("__AUTODEV_PYTHON_SHELL__", text, path.name)
            for snippet in pattern.findall(text):
                seen += 1
                self.assertTrue(snippet.startswith("autodev "), f"{path}: {snippet}")
        self.assertGreaterEqual(seen, 7)''')

    text = replace_method(text, "test_install_is_idempotent_and_preserves_user_opencode_config", '''    def test_install_is_idempotent_and_preserves_user_opencode_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            custom = target / ".opencode" / "commands" / "custom.md"
            custom.parent.mkdir(parents=True)
            custom.write_text("user-owned\\n", encoding="utf-8")
            project_json = target / "opencode.json"
            project_jsonc = target / "opencode.jsonc"
            project_json.write_text('{"agent":{"autodev-reader":{"model":"provider/reader"}}}\\n', encoding="utf-8")
            project_jsonc.write_text('// user-owned\\n{"model":"provider/default"}\\n', encoding="utf-8")

            first = opencode_adapter_assets.install_assets(target, REPO_ROOT)
            second = opencode_adapter_assets.install_assets(target, REPO_ROOT)

            self.assertEqual(len(first), len(second))
            self.assertEqual(custom.read_text(encoding="utf-8"), "user-owned\\n")
            self.assertEqual(project_json.read_text(encoding="utf-8"), '{"agent":{"autodev-reader":{"model":"provider/reader"}}}\\n')
            self.assertEqual(project_jsonc.read_text(encoding="utf-8"), '// user-owned\\n{"model":"provider/default"}\\n')
            self.assertFalse((target / ".opencode" / "autodev.json").exists())
            self.assertFalse((target / ".opencode" / "autodev.py").exists())
            self.assertFalse((target / ".opencode" / "autodev.ps1").exists())
            self.assertTrue((target / ".opencode" / "commands" / "autodev-issue-to-pr.md").is_file())
            self.assertTrue((target / ".opencode" / "agents" / "autodev-coordinator.md").is_file())''')

    text = replace_method(text, "test_role_contract_bridge_snippets_match_real_argparse_surface", '''    def test_role_contract_bridge_snippets_match_real_argparse_surface(self):
        parser = opencode_adapter_cli.build_parser()
        for contract in opencode_adapter_contract.role_contracts().values():
            accept = str(contract["accept"])
            tokens = shlex.split(accept)
            self.assertEqual(tokens[0], "autodev")
            parser.parse_args(tokens[1:])
            prepare = contract["prepare"]
            prepare_commands = prepare if isinstance(prepare, list) else [prepare]
            for command in prepare_commands:
                tokens = shlex.split(str(command))
                self.assertEqual(tokens[0], "autodev")
                parser.parse_args(tokens[1:])''')

    text = replace_method(text, "test_opencode_workflow_adapter_has_no_windows_workflow_backend", '''    def test_opencode_workflow_adapter_has_no_windows_workflow_backend(self):
        adapter = (REPO_ROOT / "automation" / "opencode_adapter_workflow.py").read_text(encoding="utf-8")
        runtime = (REPO_ROOT / "automation" / "opencode_runtime.py").read_text(encoding="utf-8")

        self.assertNotIn("windows/scripts", adapter)
        self.assertNotIn("windows/scripts", runtime)
        self.assertNotIn("issue-to-pr-cycle.ps1", adapter)
        self.assertNotIn("issue-to-pr-cycle.ps1", runtime)
        self.assertFalse((OPEN_CODE_ROOT / "autodev.py").exists())
        self.assertFalse((OPEN_CODE_ROOT / "autodev.ps1").exists())
        self.assertIn("opencode_adapter", runtime)''')

    text = text.replace("python_command=\"python-custom\"", "")
    path.write_text(text, encoding="utf-8")


def fix_role_boundary() -> None:
    path = ROOT / "tests" / "test_opencode_role_boundary_contracts.py"
    text = path.read_text(encoding="utf-8")
    text = replace_method(text, "test_standalone_role_commands_execute_directly_and_use_exact_installed_launcher", '''    def test_standalone_role_commands_execute_directly_and_use_exact_installed_launcher(self):
        roles = {
            "autodev-read.md": "reader",
            "autodev-plan.md": "planner",
            "autodev-implement.md": "implementer",
            "autodev-fix.md": "fixer",
            "autodev-verify.md": "verifier",
        }
        for name, role in roles.items():
            text = self._command_text(name)
            frontmatter = self._frontmatter(text)
            self.assertIn("subtask: false", frontmatter, name)
            self.assertIn("agent: build", frontmatter, name)
            self.assertIn(f"!`autodev role --role {role}", text, name)
            self.assertIn("--interactive-consent", text, name)
            self.assertNotIn(".opencode/autodev", text, name)
            self.assertNotIn("python3", text, name)''')
    text = replace_method(text, "test_reader_command_requires_canonical_accept_before_success", '''    def test_reader_command_requires_canonical_accept_before_success(self):
        text = self._command_text("autodev-read.md")
        self.assertIn("!`autodev role --role reader", text)
        self.assertIn("--interactive-consent", text)
        self.assertIn("Python role runner executes the isolated Reader", text)
        self.assertNotIn(" accept --role reader", text)
        self.assertIn("display-only", text)''')
    path.write_text(text, encoding="utf-8")


def main() -> int:
    fix_autodev_cli()
    fix_integration()
    fix_role_boundary()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

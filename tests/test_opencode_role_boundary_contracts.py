import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automation import opencode_runtime, workflow_stages


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


class OpenCodeRoleBoundaryContractTests(unittest.TestCase):
    def _command_text(self, name: str) -> str:
        return (OPEN_CODE_ROOT / "commands" / name).read_text(encoding="utf-8")

    def _agent_text(self, name: str) -> str:
        return (OPEN_CODE_ROOT / "agents" / name).read_text(encoding="utf-8")

    @staticmethod
    def _frontmatter(text: str) -> str:
        parts = text.split("---", 2)
        return parts[1] if len(parts) >= 3 else ""

    def test_standalone_role_commands_execute_directly_and_use_exact_installed_launcher(self):
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
            self.assertNotIn("python3", text, name)

    def test_reader_command_requires_canonical_accept_before_success(self):
        text = self._command_text("autodev-read.md")
        self.assertIn("!`autodev role --role reader", text)
        self.assertIn("--interactive-consent", text)
        self.assertIn("Python role runner executes the isolated Reader", text)
        self.assertNotIn(" accept --role reader", text)
        self.assertIn("display-only", text)

    def test_model_facing_role_artifacts_are_literal_repository_relative_paths(self):
        for name in (
            "autodev-reader.md",
            "autodev-synthesizer.md",
            "autodev-planner.md",
            "autodev-verifier.md",
        ):
            text = self._agent_text(name)
            self.assertIn("literal repository-relative path", text, name)
            self.assertIn("external_directory: deny", text, name)
            self.assertIn("Python-coordinator mode", text, name)
            self.assertIn("do not run any AutoDev `prepare` or `accept` command", text, name)

        synthesizer = self._agent_text("autodev-synthesizer.md")
        self.assertIn("`.autodev-run/current/synthesizer.md`", synthesizer)
        self.assertIn("never prepend", synthesizer)
        self.assertIn("`src/`", synthesizer)
        self.assertIn("Do not request external-directory access for AutoDev artifacts", synthesizer)

    def test_missing_reader_acceptance_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            (current / "state.json").write_text("{}\n", encoding="utf-8")

            output = io.StringIO()
            with (
                patch("automation.opencode_runtime._role_diagnostics", return_value={}),
                redirect_stdout(output),
            ):
                code = opencode_runtime._role_check(["--role", "reader", "--repo", str(repo)])

            payload = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["state"], "MISSING")
            self.assertEqual(payload["role"], "reader")
            self.assertIn("no durable accepted artifact/state", payload["reason"])


if __name__ == "__main__":
    unittest.main()

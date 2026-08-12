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
        commands = (
            "autodev-read.md",
            "autodev-plan.md",
            "autodev-implement.md",
            "autodev-fix.md",
            "autodev-verify.md",
        )
        for name in commands:
            text = self._command_text(name)
            frontmatter = self._frontmatter(text)
            self.assertIn("subtask: false", frontmatter, name)
            self.assertNotIn("subtask: true", frontmatter, name)
            self.assertIn(".opencode/autodev.json", text, name)
            self.assertIn("exact bridge launcher", text, name)
            self.assertIn("Do not probe `python`/`python3`", text, name)
            self.assertNotIn("use `python3` instead only when", text, name)
            self.assertIn("After successful accept", text, name)
            self.assertIn("then stop", text, name)

    def test_reader_command_requires_canonical_accept_before_success(self):
        text = self._command_text("autodev-read.md")
        accept = "python .opencode/autodev.py accept --role reader --input .autodev-run/current/reader-brief.md"
        self.assertIn(accept, text)
        self.assertIn("Do not claim success before this command succeeds", text)
        self.assertIn("Do not launch Synthesizer", text)

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

    def test_legacy_llm_coordinator_commands_still_fail_closed_for_manual_compatibility(self):
        for name in ("autodev-issue-to-pr.md", "autodev-resume.md"):
            text = self._command_text(name)
            self.assertIn("The very next tool invocation must be exactly", text, name)
            self.assertIn("`role-check --role <role>`", text, name)
            self.assertIn("Do not read artifacts/manifests/state", text, name)
            self.assertIn("issue another Task", text, name)
            self.assertIn("`MISSING`, `STALE`", text, name)
            self.assertIn("failed role boundary", text, name)
            self.assertIn("Follow your installed autodev-<role> contract exactly", text, name)
            self.assertIn("do not compose a new role procedure", text.casefold(), name)

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

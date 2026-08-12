import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, call, patch

from automation import (
    opencode_adapter,
    opencode_coordinator,
    opencode_cli,
    opencode_install,
    workflow_stages,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN_CODE_ROOT = REPO_ROOT / "integrations" / "opencode"


class OpenCodePythonCoordinatorTests(unittest.TestCase):
    def _repo(self, root: str, issue: int = 29) -> Path:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        (current / "state.json").write_text(
            json.dumps({"IssueNumber": issue, "AcceptedRoleArtifacts": {}}),
            encoding="utf-8",
        )
        return repo

    def test_role_execution_is_prepared_and_accepted_by_python(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            completed = SimpleNamespace(returncode=0, stdout='{"type":"text"}\n', stderr="")
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return completed

            accepted = {
                "state": "ACCEPTED",
                "role": "reader",
                "artifact": ".autodev-run/current/reader-brief.md",
                "sha256": "abc",
            }
            with patch.object(opencode_adapter, "prepare_role") as prepare, patch.object(
                opencode_adapter, "accept_role", return_value=[]
            ) as accept, patch.object(
                opencode_coordinator, "role_acceptance", return_value=accepted
            ):
                result = opencode_coordinator.run_role(
                    repo,
                    "reader",
                    runner=runner,
                    which=lambda _: "/usr/bin/opencode",
                )

            prepare.assert_called_once_with("reader", repo, "29")
            accept.assert_called_once_with(
                "reader",
                repo,
                repo / workflow_stages.CURRENT_DIR / "reader-brief.md",
            )
            self.assertEqual(result["state"], "ACCEPTED")
            command = calls[0][0]
            self.assertEqual(command[:4], ["/usr/bin/opencode", "run", "--agent", "autodev-reader"])
            self.assertIn("--dir", command)
            self.assertIn(str(repo), command)
            self.assertIn("--format", command)
            self.assertIn("json", command)
            self.assertNotIn("--model", command)
            self.assertIn("Do not run AutoDev prepare or accept commands", command[-1])

    def test_role_exit_zero_without_durable_acceptance_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            with patch.object(opencode_adapter, "prepare_role"), patch.object(
                opencode_adapter, "accept_role", return_value=[]
            ), patch.object(
                opencode_coordinator,
                "role_acceptance",
                return_value={"state": "MISSING", "reason": "not accepted"},
            ):
                with self.assertRaises(opencode_coordinator.OpenCodeCoordinatorError) as raised:
                    opencode_coordinator.run_role(
                        repo,
                        "reader",
                        runner=lambda *args, **kwargs: completed,
                        which=lambda _: "/usr/bin/opencode",
                    )
            self.assertIn("not durably accepted", str(raised.exception))

    def test_role_nonzero_exit_is_transient_and_does_not_accept(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(temp_dir)
            completed = SimpleNamespace(returncode=17, stdout="", stderr="provider unavailable")
            with patch.object(opencode_adapter, "prepare_role"), patch.object(
                opencode_adapter, "accept_role"
            ) as accept:
                with self.assertRaises(opencode_coordinator.OpenCodeCoordinatorError) as raised:
                    opencode_coordinator.run_role(
                        repo,
                        "planner",
                        runner=lambda *args, **kwargs: completed,
                        which=lambda _: "/usr/bin/opencode",
                    )
            accept.assert_not_called()
            self.assertEqual(raised.exception.classification, workflow_stages.FAILURE_TRANSIENT)
            self.assertIn("provider unavailable", str(raised.exception))

    def test_fresh_coordinator_sequences_reader_synthesizer_planner_without_llm_coordinator(self):
        cursors = [
            {"state": "RESUME", "next_action": "reader", "issue_number": 29},
            {"state": "RESUME", "next_action": "synthesizer", "issue_number": 29},
            {"state": "RESUME", "next_action": "planner", "issue_number": 29},
            {"state": "COMPLETE", "next_action": "complete", "issue_number": 29, "pr_url": "https://example/pr/1"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            opencode_coordinator.opencode_runtime, "install_workflow_guards"
        ), patch.object(
            opencode_adapter, "resolve_opencode_model_mappings", return_value={}
        ), patch.object(
            opencode_coordinator,
            "run_stage",
            side_effect=[{"state": "CONTINUE"}, {"state": "CONTINUE"}],
        ) as stage, patch.object(
            opencode_coordinator, "run_role"
        ) as role, patch.object(
            opencode_coordinator, "_resume_payload", side_effect=cursors
        ):
            result = opencode_coordinator.coordinate(Path(temp_dir), arguments="29")

        self.assertEqual(result["state"], "PR_READY")
        self.assertEqual([item.args[1] for item in role.call_args_list], ["reader", "synthesizer", "planner"])
        self.assertEqual(stage.call_args_list[0], call(Path(temp_dir).resolve(), "preflight", arguments="29"))
        self.assertEqual(stage.call_args_list[1], call(Path(temp_dir).resolve(), "prepare", arguments="29"))

    def test_resume_starts_at_synthesizer_without_preflight_or_prepare(self):
        cursors = [
            {"state": "RESUME", "next_action": "synthesizer", "issue_number": 29},
            {"state": "RESUME", "next_action": "planner", "issue_number": 29},
            {"state": "COMPLETE", "next_action": "complete", "issue_number": 29, "pr_url": "https://example/pr/1"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            opencode_coordinator.opencode_runtime, "install_workflow_guards"
        ), patch.object(
            opencode_adapter, "resolve_opencode_model_mappings", return_value={}
        ), patch.object(
            opencode_coordinator, "run_stage"
        ) as stage, patch.object(
            opencode_coordinator, "run_role"
        ) as role, patch.object(
            opencode_coordinator, "_resume_payload", side_effect=cursors
        ):
            result = opencode_coordinator.coordinate(Path(temp_dir), resume=True)

        self.assertEqual(result["state"], "PR_READY")
        stage.assert_not_called()
        self.assertEqual([item.args[1] for item in role.call_args_list], ["synthesizer", "planner"])

    def test_repair_action_prepares_the_named_fixer_kind_then_rechecks_stage(self):
        cursors = [
            {"state": "RESUME", "next_action": "fixer-local", "issue_number": 29},
            {"state": "RESUME", "next_action": "local-check", "issue_number": 29, "local_repair_attempt": 1},
            {"state": "COMPLETE", "next_action": "complete", "issue_number": 29, "pr_url": "https://example/pr/1"},
        ]
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            opencode_coordinator.opencode_runtime, "install_workflow_guards"
        ), patch.object(
            opencode_adapter, "resolve_opencode_model_mappings", return_value={}
        ), patch.object(
            opencode_coordinator, "run_role"
        ) as role, patch.object(
            opencode_coordinator, "run_stage", return_value={"state": "CONTINUE"}
        ) as stage, patch.object(
            opencode_coordinator, "_resume_payload", side_effect=cursors
        ):
            result = opencode_coordinator.coordinate(Path(temp_dir), resume=True)

        self.assertEqual(result["state"], "PR_READY")
        role.assert_called_once_with(
            Path(temp_dir).resolve(),
            "fixer",
            repair_kind="local",
            runner=ANY,
            which=None,
        )
        stage.assert_called_once_with(Path(temp_dir).resolve(), "local-check", attempt=1)

    def test_agents_have_explicit_python_coordinator_mode(self):
        for role in ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier"):
            text = (OPEN_CODE_ROOT / "agents" / f"autodev-{role}.md").read_text(encoding="utf-8")
            self.assertIn("Python-coordinator mode", text)
            self.assertIn("do not run any AutoDev `prepare` or `accept` command", text)

    def test_portable_bridge_routes_coordinate_to_python_coordinator(self):
        text = (OPEN_CODE_ROOT / "autodev.py").read_text(encoding="utf-8")
        self.assertIn('COORDINATE_COMMAND = "coordinate"', text)
        self.assertIn('module = "automation.opencode_coordinator"', text)

    def test_python_coordinator_installer_renders_launcher_into_canonical_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            launcher = "/opt/Python With Space/python3"
            opencode_install.install_assets(
                target,
                REPO_ROOT,
                python_command=launcher,
            )

            issue_command = (target / ".opencode" / "commands" / "autodev-issue-to-pr.md").read_text(encoding="utf-8")
            resume_command = (target / ".opencode" / "commands" / "autodev-resume.md").read_text(encoding="utf-8")
            for text in (issue_command, resume_command):
                self.assertIn("agent: build", text)
                self.assertNotIn(opencode_install.PYTHON_SHELL_PLACEHOLDER, text)
                self.assertIn("'/opt/Python With Space/python3' .opencode/autodev.py coordinate", text)
                self.assertNotIn("agent: autodev-coordinator", text)
            self.assertIn('--resume --arguments "$ARGUMENTS"', resume_command)


class OpenCodeCliResolverTests(unittest.TestCase):
    def test_resolver_uses_explicit_discovered_launcher(self):
        self.assertEqual(
            opencode_cli.resolve_opencode_cli(which=lambda _: "C:/tools/opencode.CMD"),
            "C:/tools/opencode.CMD",
        )

    def test_resolver_fails_when_cli_is_missing(self):
        with self.assertRaises(opencode_cli.OpenCodeCliError):
            opencode_cli.resolve_opencode_cli(which=lambda _: None)


if __name__ == "__main__":
    unittest.main()

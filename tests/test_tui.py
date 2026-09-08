from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import autodev_cli, tui_actions, tui_cli, tui_model, tui_terminal
from automation.queue_contract import Blocker, MANAGED_LABEL, QueueIssue, QueueState


class TuiRenderingTests(unittest.TestCase):
    def snapshot(self) -> tui_model.TuiSnapshot:
        return tui_model.TuiSnapshot(
            repository="owner/repo",
            repo_path="/repo",
            observed_at="2026-09-07T00:00:00Z",
            remote_observed_at="2026-09-07T00:00:00Z",
            queue={
                "managed": 2,
                "ready": 1,
                "dependency_blocked": 1,
                "attention_required": 0,
                "running": 0,
            },
            active_claims=0,
            issues=(
                tui_model.TuiIssue(10, "Ready issue", "https://example/10", "ready"),
                tui_model.TuiIssue(
                    11,
                    "Blocked issue",
                    "https://example/11",
                    "blocked",
                    ("#9 prerequisite",),
                ),
            ),
            selected_issue_number=10,
            run=tui_model.TuiRun(
                state="RESUME_EXISTING",
                issue_number=10,
                issue_title="Ready issue",
                status="Prepared",
                branch="autodev/issue-10",
                pr_url="https://example/pr/12",
                pr_number=12,
                next_stage="implementation",
                next_action="resume",
                resumable=True,
                local_check_passed=True,
            ),
            scheduler=tui_model.TuiScheduler(
                state="INSTALLED",
                backend="cron",
                backend_state="active",
                role_runtime="opencode",
                cadence_minutes=15,
                notifications="native",
            ),
            privacy=tui_model.TuiPrivacy(
                enabled=True,
                local_only=False,
                consent_mode="explicit",
                active_grants=1,
            ),
        )

    def test_render_contains_authoritative_operational_sections(self) -> None:
        rendered = tui_terminal.render(
            self.snapshot(), tui_terminal.ViewState(), width=100, height=40
        )
        self.assertIn("Repository: owner/repo", rendered)
        self.assertIn("managed=2", rendered)
        self.assertIn("Run: RESUME_EXISTING", rendered)
        self.assertIn("Branch: autodev/issue-10", rendered)
        self.assertIn("Scheduler: INSTALLED", rendered)
        self.assertIn("Runtime: opencode", rendered)
        self.assertIn("Privacy enabled=yes", rendered)
        self.assertIn("#11", rendered)
        self.assertIn("blocked by #9 prerequisite", rendered)

    def test_navigation_is_terminal_independent(self) -> None:
        state = tui_terminal.ViewState()
        state, refresh = tui_terminal.apply_key("down", self.snapshot(), state)
        self.assertEqual(state.selected_index, 1)
        self.assertFalse(refresh)
        state, _ = tui_terminal.apply_key("enter", self.snapshot(), state)
        self.assertTrue(state.detail)
        rendered = tui_terminal.render(self.snapshot(), state, width=100, height=40)
        self.assertIn("Issue #11 detail", rendered)

    def test_mutating_action_requires_explicit_confirmation(self) -> None:
        calls = []

        def execute(action, snapshot, **kwargs):
            calls.append((action, kwargs.get("issue_number")))
            return "done"

        state = tui_terminal.ViewState()
        state, _ = tui_terminal.apply_key("m", self.snapshot(), state, execute_action=execute)
        self.assertEqual(state.pending_action, "manage")
        self.assertEqual(calls, [])

        state, _ = tui_terminal.apply_key("n", self.snapshot(), state, execute_action=execute)
        self.assertEqual(calls, [])
        self.assertEqual(state.message, "Action cancelled.")

        state, _ = tui_terminal.apply_key("m", self.snapshot(), state, execute_action=execute)
        state, refresh = tui_terminal.apply_key("y", self.snapshot(), state, execute_action=execute)
        self.assertEqual(calls, [("manage", 10)])
        self.assertTrue(refresh)

    def test_open_pr_is_read_only_and_does_not_prompt(self) -> None:
        calls = []

        def execute(action, snapshot, **kwargs):
            calls.append(action)
            return snapshot.run.pr_url

        state = tui_terminal.ViewState()
        state, refresh = tui_terminal.apply_key("o", self.snapshot(), state, execute_action=execute)
        self.assertEqual(calls, ["open-pr"])
        self.assertFalse(refresh)
        self.assertEqual(state.pending_action, "")


class TuiModelTests(unittest.TestCase):
    def test_remote_queue_collection_uses_queue_state_without_mutation(self) -> None:
        snapshot = tui_model.TuiSnapshot(
            repository="owner/repo", repo_path="/repo", observed_at="now"
        )
        ready = QueueState(
            QueueIssue(
                20,
                "Ready",
                "https://example/20",
                "open",
                (MANAGED_LABEL,),
            ),
            "ready",
        )
        blocked = QueueState(
            QueueIssue(
                21,
                "Blocked",
                "https://example/21",
                "open",
                (MANAGED_LABEL,),
            ),
            "blocked",
            open_blockers=(Blocker(9, 9, "Blocker", "https://example/9", "open"),),
        )
        with mock.patch.object(
            tui_model.queue_workflow, "inspect_queue", return_value=[ready, blocked]
        ) as inspect:
            result = tui_model.collect_remote(snapshot, runner=lambda *_a, **_k: None)

        inspect.assert_called_once()
        self.assertEqual(result.queue["managed"], 2)
        self.assertEqual(result.queue["ready"], 1)
        self.assertEqual(result.selected_issue_number, 20)
        self.assertEqual(result.issues[1].blockers, ("#9 Blocker",))

    def test_remote_failure_preserves_last_known_queue(self) -> None:
        snapshot = tui_model.TuiSnapshot(
            repository="owner/repo",
            repo_path="/repo",
            observed_at="now",
            queue={"managed": 7},
            issues=(tui_model.TuiIssue(1, "Known", "u", "ready"),),
        )
        with mock.patch.object(
            tui_model.queue_workflow, "inspect_queue", side_effect=RuntimeError("offline")
        ):
            result = tui_model.collect_remote(snapshot)
        self.assertEqual(result.queue, {"managed": 7})
        self.assertEqual(result.issues, snapshot.issues)
        self.assertIn("offline", result.remote_error)


class TuiCliTests(unittest.TestCase):
    def test_top_level_cli_routes_tui_without_opencode(self) -> None:
        with mock.patch.object(tui_cli, "run_cli", return_value=0) as run_tui, mock.patch.object(
            autodev_cli.opencode_entrypoint, "run"
        ) as opencode:
            code = autodev_cli.run(["tui", "--once"])
        self.assertEqual(code, 0)
        run_tui.assert_called_once_with(["--once"])
        opencode.assert_not_called()

    def test_tui_rejects_unbounded_remote_polling_rate(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = tui_cli.run_cli(
            ["--refresh-seconds", "1", "--once"],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 2)
        self.assertIn("at least 10", stderr.getvalue())

    def test_once_json_uses_terminal_independent_snapshot(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        snapshot = tui_model.TuiSnapshot(
            repository="owner/repo", repo_path="/repo", observed_at="now"
        )
        with mock.patch.object(
            tui_model, "resolve_repository", return_value=(Path("/repo"), "owner/repo")
        ), mock.patch.object(tui_model, "collect_local", return_value=snapshot), mock.patch.object(
            tui_model, "collect_remote", return_value=snapshot
        ):
            code = tui_cli.run_cli(["--once", "--json"], stdout=stdout, stderr=stderr)
        self.assertEqual(code, 0)
        self.assertIn('"repository": "owner/repo"', stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()

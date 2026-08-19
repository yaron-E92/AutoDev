from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import issue_queue


class FakeGitHub:
    def __init__(self):
        self.repo = "owner/repo"
        self.labels = {
            issue_queue.READY_LABEL,
            issue_queue.BLOCKED_LABEL,
            issue_queue.RUNNING_LABEL,
        }
        self.issues: dict[int, dict[str, object]] = {}
        self.blocked_by: dict[int, list[int]] = {}
        self.calls: list[list[str]] = []
        self.mutations: list[list[str]] = []
        self.dependencies_available = True

    def add_issue(
        self,
        number: int,
        *,
        state: str = "OPEN",
        labels: list[str] | None = None,
        title: str | None = None,
    ) -> None:
        self.issues[number] = {
            "id": 1000 + number,
            "number": number,
            "title": title or f"Issue {number}",
            "url": f"https://github.test/owner/repo/issues/{number}",
            "html_url": f"https://github.test/owner/repo/issues/{number}",
            "state": state,
            "labels": list(labels or []),
        }
        self.labels.update(labels or [])

    def set_blockers(self, issue: int, blockers: list[int]) -> None:
        self.blocked_by[issue] = list(blockers)

    def _issue_json(self, issue: dict[str, object]) -> dict[str, object]:
        return {
            "id": issue["id"],
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["url"],
            "html_url": issue["html_url"],
            "state": issue["state"],
            "labels": [{"name": name} for name in issue["labels"]],
        }

    def __call__(self, argv, **kwargs):
        args = list(argv)
        self.calls.append(args)
        if not args or args[0] != "gh":
            raise AssertionError(f"non-GitHub command invoked: {args}")
        command = args[1:]
        if command[:2] == ["repo", "view"]:
            return SimpleNamespace(returncode=0, stdout=self.repo + "\n", stderr="")
        if command[:2] == ["label", "list"]:
            payload = [{"name": name} for name in sorted(self.labels)]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if command[:2] == ["label", "create"]:
            self.mutations.append(args)
            self.labels.add(command[2])
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["issue", "list"]:
            payload = [self._issue_json(self.issues[number]) for number in sorted(self.issues)]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if command[:2] == ["issue", "view"]:
            number = int(command[2])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(self._issue_json(self.issues[number])),
                stderr="",
            )
        if command[:2] == ["issue", "edit"]:
            self.mutations.append(args)
            number = int(command[2])
            labels = self.issues[number]["labels"]
            assert isinstance(labels, list)
            index = 3
            while index < len(command):
                token = command[index]
                if token == "--add-label":
                    value = command[index + 1]
                    if value not in labels:
                        labels.append(value)
                    index += 2
                    continue
                if token == "--remove-label":
                    value = command[index + 1]
                    if value in labels:
                        labels.remove(value)
                    index += 2
                    continue
                index += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command and command[0] == "api":
            endpoint = command[-1]
            if "/dependencies/blocked_by" not in endpoint:
                raise AssertionError(f"unexpected API endpoint: {endpoint}")
            if not self.dependencies_available:
                return SimpleNamespace(returncode=1, stdout="", stderr="404 not found")
            path = endpoint.split("?", 1)[0]
            parts = path.split("/")
            issue_index = parts.index("issues") + 1
            issue_number = int(parts[issue_index])
            if "--method" in command:
                method = command[command.index("--method") + 1]
            else:
                method = "GET"
            if method == "GET":
                payload = [
                    self._issue_json(self.issues[number])
                    for number in self.blocked_by.get(issue_number, [])
                ]
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            if method == "DELETE":
                self.mutations.append(args)
                blocker_id = int(parts[-1])
                relationships = self.blocked_by.get(issue_number, [])
                self.blocked_by[issue_number] = [
                    number
                    for number in relationships
                    if int(self.issues[number]["id"]) != blocker_id
                ]
                return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        raise AssertionError(f"unexpected gh invocation: {args}")


class IssueQueueTests(unittest.TestCase):
    def test_unmanaged_is_not_enrolled_and_managed_unblocked_becomes_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(1, labels=[issue_queue.READY_LABEL, "priority:high"])
            fake.add_issue(2, labels=[issue_queue.MANAGED_LABEL, "area:python"])

            states, _ = issue_queue.reconcile_queue(repo, fake.repo, runner=fake)

            self.assertNotIn(issue_queue.READY_LABEL, fake.issues[1]["labels"])
            self.assertIn("priority:high", fake.issues[1]["labels"])
            self.assertIn(issue_queue.READY_LABEL, fake.issues[2]["labels"])
            self.assertIn("area:python", fake.issues[2]["labels"])
            reasons = {state.issue.number: state.reason for state in states}
            self.assertEqual(reasons, {1: "unmanaged", 2: "ready"})

    def test_multiple_blockers_remain_blocked_until_final_one_closes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(10, labels=[issue_queue.MANAGED_LABEL])
            fake.add_issue(20)
            fake.add_issue(21)
            fake.set_blockers(10, [21, 20])

            states, _ = issue_queue.reconcile_queue(repo, fake.repo, runner=fake)
            state = next(item for item in states if item.issue.number == 10)
            self.assertEqual([item.number for item in state.open_blockers], [20, 21])
            self.assertIn(issue_queue.BLOCKED_LABEL, fake.issues[10]["labels"])
            self.assertNotIn(issue_queue.READY_LABEL, fake.issues[10]["labels"])

            fake.issues[20]["state"] = "CLOSED"
            states, _ = issue_queue.reconcile_queue(repo, fake.repo, runner=fake)
            state = next(item for item in states if item.issue.number == 10)
            self.assertEqual([item.number for item in state.open_blockers], [21])
            self.assertEqual(fake.blocked_by[10], [21])
            self.assertEqual(state.removed_closed_dependencies, (20,))
            self.assertIn(issue_queue.BLOCKED_LABEL, fake.issues[10]["labels"])

            fake.issues[21]["state"] = "CLOSED"
            states, _ = issue_queue.reconcile_queue(repo, fake.repo, runner=fake)
            state = next(item for item in states if item.issue.number == 10)
            self.assertEqual(state.reason, "ready")
            self.assertEqual(fake.blocked_by[10], [])
            self.assertEqual(state.removed_closed_dependencies, (21,))
            self.assertIn(issue_queue.READY_LABEL, fake.issues[10]["labels"])
            self.assertNotIn(issue_queue.BLOCKED_LABEL, fake.issues[10]["labels"])

    def test_reconciliation_is_idempotent_after_state_converges(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(1, labels=[issue_queue.MANAGED_LABEL])

            issue_queue.reconcile_queue(repo, fake.repo, runner=fake)
            fake.mutations.clear()
            issue_queue.reconcile_queue(repo, fake.repo, runner=fake)

            self.assertEqual(fake.mutations, [])

    def test_attention_running_policy_and_closed_issues_are_not_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(
                1,
                labels=[issue_queue.MANAGED_LABEL, issue_queue.ATTENTION_LABEL, issue_queue.READY_LABEL],
            )
            fake.add_issue(
                2,
                labels=[issue_queue.MANAGED_LABEL, issue_queue.RUNNING_LABEL, issue_queue.READY_LABEL],
            )
            fake.add_issue(
                3,
                state="CLOSED",
                labels=[issue_queue.MANAGED_LABEL, issue_queue.BLOCKED_LABEL],
            )
            (repo / ".autodev").mkdir()
            (repo / issue_queue.QUEUE_CONFIG).write_text(
                json.dumps({"version": 1, "autonomous_execution": False}),
                encoding="utf-8",
            )
            fake.add_issue(4, labels=[issue_queue.MANAGED_LABEL, issue_queue.READY_LABEL])

            states, _ = issue_queue.reconcile_queue(repo, fake.repo, runner=fake)
            reasons = {state.issue.number: state.reason for state in states}

            self.assertEqual(reasons[1], "attention")
            self.assertEqual(reasons[2], "running")
            self.assertEqual(reasons[3], "closed")
            self.assertEqual(reasons[4], "policy-excluded")
            for number in (1, 2, 3, 4):
                self.assertNotIn(issue_queue.READY_LABEL, fake.issues[number]["labels"])
            self.assertNotIn(issue_queue.BLOCKED_LABEL, fake.issues[3]["labels"])
            self.assertIn(issue_queue.MANAGED_LABEL, fake.issues[3]["labels"])

    def test_status_uses_authoritative_dependency_state_not_stale_labels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(1, labels=[issue_queue.MANAGED_LABEL, issue_queue.READY_LABEL])
            fake.add_issue(2, labels=[issue_queue.MANAGED_LABEL, issue_queue.READY_LABEL])
            fake.add_issue(3, labels=[issue_queue.MANAGED_LABEL, issue_queue.ATTENTION_LABEL])
            fake.add_issue(20)
            fake.set_blockers(2, [20])

            states = issue_queue.inspect_queue(repo, fake.repo, runner=fake)
            summary = issue_queue.queue_summary(states)

            self.assertEqual(summary["managed"], 3)
            self.assertEqual(summary["ready"], 1)
            self.assertEqual(summary["dependency_blocked"], 1)
            self.assertEqual(summary["attention_required"], 1)

    def test_explanation_uses_deterministically_sorted_authoritative_blockers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(50, labels=[issue_queue.MANAGED_LABEL], title="Target")
            fake.add_issue(2, title="Second")
            fake.add_issue(1, title="First")
            fake.set_blockers(50, [2, 1])

            issue = issue_queue.fetch_issue(repo, fake.repo, 50, runner=fake)
            blockers = issue_queue.list_blockers(repo, fake.repo, 50, runner=fake)
            state = issue_queue.classify_issue(issue, blockers, issue_queue.QueuePolicy())

            self.assertEqual([item.number for item in blockers], [1, 2])
            self.assertEqual(
                issue_queue.explain_state(state),
                "#50 blocked by: #1 First, #2 Second",
            )

    def test_native_dependency_api_failure_fails_closed_without_prose_inference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(1, labels=[issue_queue.MANAGED_LABEL])
            fake.dependencies_available = False

            with self.assertRaises(issue_queue.QueueError) as caught:
                issue_queue.reconcile_queue(repo, fake.repo, runner=fake)

            self.assertIn("will not infer blockers from issue prose", str(caught.exception))
            self.assertNotIn(issue_queue.READY_LABEL, fake.issues[1]["labels"])

    def test_cli_reconcile_status_and_explain_require_only_github_operations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = FakeGitHub()
            fake.add_issue(1, labels=[issue_queue.MANAGED_LABEL])

            reconcile_out = io.StringIO()
            self.assertEqual(
                issue_queue.run_cli(
                    ["reconcile", "--github-repo", fake.repo],
                    repo=repo,
                    runner=fake,
                    stdout=reconcile_out,
                ),
                0,
            )
            self.assertIn("managed=1 ready=1", reconcile_out.getvalue())

            status_out = io.StringIO()
            self.assertEqual(
                issue_queue.run_cli(
                    ["status", "--github-repo", fake.repo],
                    repo=repo,
                    runner=fake,
                    stdout=status_out,
                ),
                0,
            )
            self.assertIn("ready=1", status_out.getvalue())

            explain_out = io.StringIO()
            self.assertEqual(
                issue_queue.run_cli(
                    ["explain", "1", "--github-repo", fake.repo],
                    repo=repo,
                    runner=fake,
                    stdout=explain_out,
                ),
                0,
            )
            self.assertIn("eligible for autonomous execution", explain_out.getvalue())
            self.assertTrue(all(call[0] == "gh" for call in fake.calls))


if __name__ == "__main__":
    unittest.main()

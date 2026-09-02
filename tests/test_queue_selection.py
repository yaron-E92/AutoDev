from __future__ import annotations

from automation import queue_cli, queue_contract

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import queue_selection


class SelectionGitHub:
    def __init__(self):
        self.repo = "owner/repo"
        self.labels = {
            queue_contract.MANAGED_LABEL,
            queue_contract.READY_LABEL,
            queue_contract.BLOCKED_LABEL,
            queue_contract.ATTENTION_LABEL,
            queue_contract.RUNNING_LABEL,
        }
        self.label_metadata: dict[str, dict[str, str]] = {}
        for name in self.labels:
            color, description = queue_contract.LABEL_SPECS.get(
                name,
                ("ededed", ""),
            )
            self.label_metadata[name] = {
                "name": name,
                "color": color,
                "description": description,
            }
        self.issues: dict[int, dict[str, object]] = {}
        self.blocked_by: dict[int, list[int]] = {}
        self.open_prs: list[dict[str, str]] = []
        self.pr_states: dict[str, dict[str, object]] = {}
        self.calls: list[list[str]] = []
        self.mutations: list[list[str]] = []

    def _ensure_label_metadata(self, name: str) -> None:
        if name in self.label_metadata:
            return
        color, description = queue_contract.LABEL_SPECS.get(
            name,
            ("ededed", ""),
        )
        self.label_metadata[name] = {
            "name": name,
            "color": color,
            "description": description,
        }

    def add_issue(
        self,
        number: int,
        *,
        state: str = "OPEN",
        labels: list[str] | None = None,
        created_at: str | None = None,
        milestone: str = "",
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
            "createdAt": created_at or f"2026-01-{min(number, 28):02d}T00:00:00Z",
            "milestone": {"title": milestone} if milestone else None,
            "body": "",
        }
        for name in labels or []:
            self.labels.add(name)
            self._ensure_label_metadata(name)

    def add_pr(self, issue_number: int) -> None:
        url = f"https://github.test/owner/repo/pull/{issue_number}"
        self.open_prs.append(
            {
                "headRefName": f"autodev/issue-{issue_number}-work",
                "url": url,
            }
        )
        self.pr_states[url] = {"state": "OPEN", "mergedAt": None}

    def set_pr_state(self, issue_number: int, state: str, *, merged: bool = False) -> str:
        url = f"https://github.test/owner/repo/pull/{issue_number}"
        self.pr_states[url] = {
            "state": state.upper(),
            "mergedAt": "2026-09-02T08:00:00Z" if merged else None,
        }
        return url

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
            "createdAt": issue["createdAt"],
            "milestone": issue["milestone"],
            "body": issue["body"],
        }

    @staticmethod
    def _option(command: list[str], name: str, default: str = "") -> str:
        if name not in command:
            return default
        index = command.index(name)
        return command[index + 1] if index + 1 < len(command) else default

    def __call__(self, argv, **kwargs):
        args = list(argv)
        self.calls.append(args)
        if not args or args[0] != "gh":
            raise AssertionError(f"selection invoked a non-GitHub command: {args}")
        command = args[1:]
        if command[:2] == ["repo", "view"]:
            return SimpleNamespace(returncode=0, stdout=self.repo + "\n", stderr="")
        if command[:2] == ["label", "list"]:
            payload = [
                self.label_metadata[name]
                for name in sorted(self.labels)
            ]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
        if command[:2] == ["label", "create"]:
            self.mutations.append(args)
            name = command[2]
            self.labels.add(name)
            self.label_metadata[name] = {
                "name": name,
                "color": self._option(command, "--color"),
                "description": self._option(command, "--description"),
            }
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["label", "edit"]:
            self.mutations.append(args)
            name = command[2]
            self.labels.add(name)
            self._ensure_label_metadata(name)
            self.label_metadata[name]["color"] = self._option(
                command,
                "--color",
                self.label_metadata[name]["color"],
            )
            self.label_metadata[name]["description"] = self._option(
                command,
                "--description",
                self.label_metadata[name]["description"],
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["issue", "list"]:
            payload = [
                self._issue_json(self.issues[number])
                for number in sorted(self.issues)
            ]
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
                    self.labels.add(value)
                    self._ensure_label_metadata(value)
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
        if command[:2] == ["pr", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(self.open_prs),
                stderr="",
            )
        if command[:2] == ["pr", "view"]:
            url = command[2]
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(self.pr_states[url]),
                stderr="",
            )
        if command and command[0] == "api":
            endpoint = command[-1]
            path = endpoint.split("?", 1)[0]
            parts = path.split("/")
            issue_index = parts.index("issues") + 1
            issue_number = int(parts[issue_index])
            method = (
                command[command.index("--method") + 1]
                if "--method" in command
                else "GET"
            )
            if method == "GET":
                payload = [
                    self._issue_json(self.issues[number])
                    for number in self.blocked_by.get(issue_number, [])
                ]
                return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
            if method == "DELETE":
                self.mutations.append(args)
                blocker_id = int(parts[-1])
                self.blocked_by[issue_number] = [
                    number
                    for number in self.blocked_by.get(issue_number, [])
                    if int(self.issues[number]["id"]) != blocker_id
                ]
                return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        raise AssertionError(f"unexpected GitHub command: {args}")


class QueueSelectionTests(unittest.TestCase):
    def _ready(self, fake: SelectionGitHub, number: int, **kwargs) -> None:
        labels = list(kwargs.pop("labels", []))
        labels.append(queue_contract.MANAGED_LABEL)
        fake.add_issue(number, labels=labels, **kwargs)

    def _write_roadmap(self, repo: Path, text: str) -> None:
        path = repo / queue_selection.ROADMAP_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_oldest_ready_issue_wins_without_roadmap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 20, created_at="2026-01-02T00:00:00Z")
            self._ready(fake, 5, created_at="2026-01-03T00:00:00Z")
            self._ready(fake, 99, created_at="2026-01-01T00:00:00Z")

            result = queue_selection.select_next(repo, fake.repo, runner=fake)

            self.assertEqual(result.state, "SELECTED")
            self.assertEqual(result.issue_number, 99)
            self.assertEqual(result.source, "oldest")

    def test_explicit_roadmap_issue_wins_over_older_eligible_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 10, created_at="2026-01-01T00:00:00Z")
            self._ready(fake, 20, created_at="2026-02-01T00:00:00Z")
            self._write_roadmap(
                repo,
                "version: 1\npriority:\n  - issue: 20\nfallback: oldest\n",
            )

            result = queue_selection.select_next(repo, fake.repo, runner=fake)

            self.assertEqual(result.issue_number, 20)
            self.assertEqual(result.source, "roadmap:issue")

    def test_blocked_and_attention_roadmap_issues_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 10, created_at="2026-01-03T00:00:00Z")
            self._ready(fake, 20, created_at="2026-01-01T00:00:00Z")
            self._ready(
                fake,
                30,
                labels=[queue_contract.ATTENTION_LABEL],
                created_at="2026-01-02T00:00:00Z",
            )
            fake.add_issue(200)
            fake.set_blockers(20, [200])
            self._write_roadmap(
                repo,
                "version: 1\npriority:\n  - issue: 20\n  - issue: 30\nfallback: oldest\n",
            )

            result = queue_selection.select_next(repo, fake.repo, runner=fake)

            self.assertEqual(result.issue_number, 10)
            self.assertEqual(result.source, "oldest")
            self.assertTrue(
                any("#20" in item and "blocked" in item for item in result.ineligible)
            )
            self.assertTrue(
                any("#30" in item and "attention" in item for item in result.ineligible)
            )

    def test_milestone_and_label_rules_rank_only_eligible_issues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 1, created_at="2026-01-01T00:00:00Z")
            self._ready(
                fake,
                2,
                milestone="MVP",
                created_at="2026-01-02T00:00:00Z",
            )
            self._ready(
                fake,
                3,
                labels=["priority:high"],
                created_at="2026-01-03T00:00:00Z",
            )
            self._write_roadmap(
                repo,
                "version: 1\npriority:\n  - label: priority:high\n  - milestone: MVP\nfallback: oldest\n",
            )

            result = queue_selection.select_next(repo, fake.repo, runner=fake)

            self.assertEqual(result.issue_number, 3)
            self.assertEqual(result.source, "roadmap:label")

    def test_explicit_issue_priority_is_stronger_than_broader_rule_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 1, labels=["priority:high"])
            self._ready(fake, 2)
            self._write_roadmap(
                repo,
                "version: 1\npriority:\n  - label: priority:high\n  - issue: 2\nfallback: oldest\n",
            )

            result = queue_selection.select_next(repo, fake.repo, runner=fake)

            self.assertEqual(result.issue_number, 2)
            self.assertEqual(result.source, "roadmap:issue")

    def test_no_eligible_work_is_a_successful_no_ready_work_outcome(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 1, labels=[queue_contract.ATTENTION_LABEL])
            output = io.StringIO()

            code = queue_cli.run_cli(
                ["next", "--github-repo", fake.repo, "--json"],
                repo=repo,
                runner=fake,
                stdout=output,
            )

            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["state"], "NO_READY_WORK")

    def test_existing_resumable_run_wins_before_unrelated_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 10)
            self._ready(fake, 20)

            result = queue_selection.select_next(
                repo,
                fake.repo,
                runner=fake,
                existing_run_inspector=lambda _repo: queue_selection.ExistingRun(
                    "RESUME_EXISTING",
                    issue_number=20,
                    branch="autodev/issue-20-work",
                    next_stage="plan-created",
                    next_action="planner",
                    reason="resume durable run first",
                ),
            )

            self.assertEqual(result.state, "RESUME_EXISTING")
            self.assertEqual(result.issue_number, 20)
            self.assertEqual(result.source, "existing-run")

    def test_active_autodev_pr_excludes_otherwise_ready_issue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 1, created_at="2026-01-01T00:00:00Z")
            self._ready(fake, 2, created_at="2026-01-02T00:00:00Z")
            fake.add_pr(1)

            result = queue_selection.select_next(repo, fake.repo, runner=fake)

            self.assertEqual(result.issue_number, 2)
            self.assertTrue(
                any(
                    "#1" in item and "active AutoDev PR" in item
                    for item in result.ineligible
                )
            )


    def test_ready_run_with_open_pr_blocks_unrelated_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 10)
            self._ready(fake, 20)
            pr_url = fake.set_pr_state(10, "OPEN")

            result = queue_selection.select_next(
                repo,
                fake.repo,
                runner=fake,
                existing_run_inspector=lambda _repo: queue_selection.ExistingRun(
                    "AWAITING_MERGE",
                    issue_number=10,
                    branch="autodev/issue-10-work",
                    pr_url=pr_url,
                    reason="waiting for merge",
                    next_action="wait for pull request merge",
                ),
            )

            self.assertEqual(result.state, "PR_READY")
            self.assertEqual(result.issue_number, 10)

    def test_merged_ready_pr_allows_next_issue_in_same_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 20)
            pr_url = fake.set_pr_state(10, "CLOSED", merged=True)

            result = queue_selection.select_next(
                repo,
                fake.repo,
                runner=fake,
                existing_run_inspector=lambda _repo: queue_selection.ExistingRun(
                    "AWAITING_MERGE",
                    issue_number=10,
                    branch="autodev/issue-10-work",
                    pr_url=pr_url,
                ),
            )

            self.assertEqual(result.state, "SELECTED")
            self.assertEqual(result.issue_number, 20)

    def test_closed_unmerged_ready_pr_requires_attention(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 20)
            pr_url = fake.set_pr_state(10, "CLOSED")

            result = queue_selection.select_next(
                repo,
                fake.repo,
                runner=fake,
                existing_run_inspector=lambda _repo: queue_selection.ExistingRun(
                    "AWAITING_MERGE",
                    issue_number=10,
                    branch="autodev/issue-10-work",
                    pr_url=pr_url,
                ),
            )

            self.assertEqual(result.state, "ATTENTION_REQUIRED")
            self.assertEqual(result.issue_number, 10)
            self.assertIn("closed without being merged", result.explanation)

    def test_malformed_roadmap_fails_safely_and_actionably(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 1)
            self._write_roadmap(
                repo,
                "version: 1\npriority:\n  - issue: nope\nfallback: oldest\n",
            )
            err = io.StringIO()

            code = queue_cli.run_cli(
                ["next", "--github-repo", fake.repo],
                repo=repo,
                runner=fake,
                stderr=err,
            )

            self.assertEqual(code, 2)
            self.assertIn("issue priority must be a positive integer", err.getvalue())

    def test_dry_run_is_read_only_and_repeated_selection_is_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            fake = SelectionGitHub()
            self._ready(fake, 1, created_at="2026-01-01T00:00:00Z")
            self._ready(fake, 2, created_at="2026-01-02T00:00:00Z")

            first = queue_selection.select_next(
                repo,
                fake.repo,
                runner=fake,
                dry_run=True,
            )
            second = queue_selection.select_next(
                repo,
                fake.repo,
                runner=fake,
                dry_run=True,
            )

            self.assertEqual(first.issue_number, second.issue_number)
            self.assertEqual(first.issue_number, 1)
            self.assertEqual(fake.mutations, [])
            self.assertTrue(all(call[0] == "gh" for call in fake.calls))


if __name__ == "__main__":
    unittest.main()

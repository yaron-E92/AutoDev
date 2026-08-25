from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import autodev_cli, manage_cli, opencode_entrypoint
from automation.queue_contract import LABEL_SPECS, MANAGED_LABEL, READY_LABEL


class Completed:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGitHub:
    def __init__(self) -> None:
        self.repository = "example/widgets"
        self.labels = set(LABEL_SPECS)
        self.issues: dict[int, dict[str, object]] = {}
        self.pull_requests: set[int] = set()
        self.calls: list[list[str]] = []
        self.fail_repository_resolution = False

    def add_issue(
        self,
        number: int,
        title: str,
        *,
        state: str = "OPEN",
        labels: tuple[str, ...] = (),
    ) -> None:
        self.issues[number] = {
            "number": number,
            "title": title,
            "url": f"https://github.com/{self.repository}/issues/{number}",
            "state": state,
            "labels": [{"name": label} for label in labels],
            "createdAt": f"2026-01-{min(number, 28):02d}T00:00:00Z",
            "milestone": None,
        }

    def __call__(self, argv: list[str], **_: object) -> Completed:
        self.calls.append(list(argv))
        if argv[:3] == ["gh", "repo", "view"]:
            if self.fail_repository_resolution:
                return Completed(1, stderr="authentication required")
            return Completed(stdout=self.repository + "\n")

        if argv[:3] == ["gh", "label", "list"]:
            return Completed(stdout=json.dumps([{"name": value} for value in sorted(self.labels)]))
        if argv[:3] == ["gh", "label", "create"]:
            self.labels.add(argv[3])
            return Completed()

        if argv[:3] == ["gh", "issue", "list"]:
            # `gh issue list` is intentionally the manage --all/list source; unlike
            # the REST issues collection it does not mix pull requests into results.
            return Completed(stdout=json.dumps(list(self.issues.values())))
        if argv[:3] == ["gh", "issue", "view"]:
            number = int(argv[3])
            if number in self.pull_requests:
                return Completed(1, stderr=f"#{number} is a pull request, not an issue")
            issue = self.issues.get(number)
            if issue is None:
                return Completed(1, stderr=f"issue #{number} not found")
            return Completed(stdout=json.dumps(issue))
        if argv[:3] == ["gh", "issue", "edit"]:
            number = int(argv[3])
            if number in self.pull_requests:
                return Completed(1, stderr="pull request is not an issue")
            issue = self.issues[number]
            label = argv[argv.index("--add-label") + 1]
            labels = {
                str(item["name"])
                for item in issue.get("labels", [])
                if isinstance(item, dict) and item.get("name")
            }
            labels.add(label)
            issue["labels"] = [{"name": value} for value in sorted(labels)]
            return Completed()
        raise AssertionError(f"unexpected command: {argv}")


class ManageCliTests(unittest.TestCase):
    def _repo(self, temp: str) -> Path:
        repo = Path(temp) / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        return repo

    def test_single_issue_accepts_hash_form_and_preserves_existing_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            github = FakeGitHub()
            github.add_issue(12, "Manage me", labels=("priority:high",))
            out = io.StringIO()

            code = manage_cli.run_cli(["#12", "--repo", str(repo)], runner=github, stdout=out)

            self.assertEqual(code, 0)
            labels = {item["name"] for item in github.issues[12]["labels"]}
            self.assertEqual(labels, {"priority:high", MANAGED_LABEL})
            self.assertNotIn(READY_LABEL, labels)
            self.assertIn("Managed issue #12", out.getvalue())

    def test_single_issue_ensures_managed_label_through_canonical_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            github = FakeGitHub()
            github.labels.remove(MANAGED_LABEL)
            github.add_issue(3, "Bootstrap label")

            code = manage_cli.run_cli(["3", "--repo", str(repo)], runner=github)

            self.assertEqual(code, 0)
            self.assertIn(MANAGED_LABEL, github.labels)
            create_calls = [call for call in github.calls if call[:3] == ["gh", "label", "create"]]
            self.assertEqual([call[3] for call in create_calls], [MANAGED_LABEL])

    def test_already_managed_issue_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            github = FakeGitHub()
            github.add_issue(7, "Already", labels=(MANAGED_LABEL, "keep-me"))
            out = io.StringIO()

            code = manage_cli.run_cli(["7", "--repo", str(repo)], runner=github, stdout=out)

            self.assertEqual(code, 0)
            self.assertFalse(any(call[:3] == ["gh", "issue", "edit"] for call in github.calls))
            self.assertIn("already managed", out.getvalue())

    def test_all_manages_only_open_issues_and_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            github = FakeGitHub()
            github.add_issue(1, "New", labels=("keep",))
            github.add_issue(2, "Existing", labels=(MANAGED_LABEL,))
            github.add_issue(3, "Closed", state="CLOSED")
            github.pull_requests.add(4)
            out = io.StringIO()

            code = manage_cli.run_cli(["--all", "--repo", str(repo)], runner=github, stdout=out)

            self.assertEqual(code, 0)
            self.assertIn(MANAGED_LABEL, {item["name"] for item in github.issues[1]["labels"]})
            self.assertIn("keep", {item["name"] for item in github.issues[1]["labels"]})
            self.assertNotIn(MANAGED_LABEL, {item["name"] for item in github.issues[3]["labels"]})
            edited = [int(call[3]) for call in github.calls if call[:3] == ["gh", "issue", "edit"]]
            self.assertEqual(edited, [1])
            self.assertIn("newly-managed=1 already-managed=1", out.getvalue())

    def test_list_is_read_only_and_has_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            github = FakeGitHub()
            github.add_issue(1, "Managed", labels=(MANAGED_LABEL,))
            github.add_issue(2, "Unmanaged")
            github.add_issue(3, "Closed managed", state="CLOSED", labels=(MANAGED_LABEL,))
            out = io.StringIO()

            code = manage_cli.run_cli(
                ["--list", "--json", "--repo", str(repo)], runner=github, stdout=out
            )

            self.assertEqual(code, 0)
            value = json.loads(out.getvalue())
            self.assertEqual(value["repository"], github.repository)
            self.assertEqual(value["count"], 1)
            self.assertEqual([item["number"] for item in value["issues"]], [1])
            self.assertFalse(any(call[1:3] == ["label", "list"] for call in github.calls))
            self.assertFalse(any(call[:3] == ["gh", "issue", "edit"] for call in github.calls))

    def test_closed_issue_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            github = FakeGitHub()
            github.add_issue(5, "Closed", state="CLOSED")
            err = io.StringIO()

            code = manage_cli.run_cli(["5", "--repo", str(repo)], runner=github, stderr=err)

            self.assertEqual(code, 2)
            self.assertIn("not open", err.getvalue())

    def test_pull_request_number_is_rejected_and_all_never_edits_prs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            github = FakeGitHub()
            github.add_issue(1, "Issue")
            github.pull_requests.add(9)
            err = io.StringIO()

            single = manage_cli.run_cli(["9", "--repo", str(repo)], runner=github, stderr=err)
            self.assertEqual(single, 2)
            self.assertIn("pull request", err.getvalue())

            github.calls.clear()
            all_code = manage_cli.run_cli(["--all", "--repo", str(repo)], runner=github)
            self.assertEqual(all_code, 0)
            edited = [int(call[3]) for call in github.calls if call[:3] == ["gh", "issue", "edit"]]
            self.assertEqual(edited, [1])
            self.assertNotIn(9, edited)

    def test_repository_and_auth_failures_are_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing_git = Path(temp) / "not-a-repo"
            missing_git.mkdir()
            github = FakeGitHub()
            err = io.StringIO()
            code = manage_cli.run_cli(["--list", "--repo", str(missing_git)], runner=github, stderr=err)
            self.assertEqual(code, 2)
            self.assertIn("not a Git repository root", err.getvalue())
            self.assertEqual(github.calls, [])

            repo = self._repo(temp)
            github.fail_repository_resolution = True
            err = io.StringIO()
            code = manage_cli.run_cli(["--list", "--repo", str(repo)], runner=github, stderr=err)
            self.assertEqual(code, 2)
            self.assertIn("authentication required", err.getvalue())

    def test_top_level_manage_routes_without_opencode(self) -> None:
        with mock.patch.object(manage_cli, "run_cli", return_value=0) as manage_run, mock.patch.object(
            opencode_entrypoint, "run", return_value=99
        ) as opencode_run:
            code = autodev_cli.run(["manage", "123"])

        self.assertEqual(code, 0)
        manage_run.assert_called_once_with(["123"])
        opencode_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

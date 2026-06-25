import io
import json
import tempfile
import unittest
from pathlib import Path

from automation.create_issues_from_description import (
    build_issue_draft,
    run,
    select_repository,
    split_descriptions,
)


class CreateIssuesFromDescriptionTests(unittest.TestCase):
    def test_splits_description_file_on_markdown_headings_and_separators(self):
        text = """## First issue
Fix stale docs for the automation runner.

---

## Second issue
Add a dry-run mode to the helper.
"""

        self.assertEqual(
            split_descriptions(text),
            [
                "Fix stale docs for the automation runner.",
                "Add a dry-run mode to the helper.",
            ],
        )

    def test_builds_structured_issue_draft_from_short_description(self):
        draft = build_issue_draft("Add dry-run mode to the issue creator for safer testing.")

        self.assertEqual(draft.title, "Add dry-run mode to the issue creator")
        self.assertIn("## Context", draft.body)
        self.assertIn("## Goal", draft.body)
        self.assertIn("## Acceptance Criteria", draft.body)
        self.assertIn("- [ ]", draft.body)
        self.assertIn("automation", draft.labels)

    def test_selects_explicit_repo_before_repo_map(self):
        selection = select_repository(
            "Update PHOODAB onboarding",
            explicit_repo="owner/ManualRepo",
            repo_map={"phoodab": "owner/PHOODAB"},
        )

        self.assertEqual(selection.repository, "owner/ManualRepo")
        self.assertFalse(selection.ambiguous)

    def test_refuses_ambiguous_repo_map_matches(self):
        selection = select_repository(
            "Improve codex automation docs",
            explicit_repo=None,
            repo_map={
                "codex": "owner/CodexAutomation",
                "codex automation": "owner/AutoDev",
            },
        )

        self.assertTrue(selection.ambiguous)
        self.assertIsNone(selection.repository)
        self.assertEqual(selection.candidates, ["owner/AutoDev", "owner/CodexAutomation"])

    def test_default_dry_run_prints_gh_command_without_creating_issue(self):
        output = io.StringIO()

        exit_code = run(
            ["--description", "Add Windows support to AutoDev", "--repo", "owner/AutoDev"],
            stdout=output,
            gh_runner=lambda command: self.fail(f"unexpected gh call: {command}"),
        )

        self.assertEqual(exit_code, 0)
        value = output.getvalue()
        self.assertIn("Mode: dry-run", value)
        self.assertIn("Repository: owner/AutoDev", value)
        self.assertIn("gh issue create --repo owner/AutoDev", value)

    def test_create_uses_gh_issue_create_and_writes_creation_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "creation-log.jsonl"
            calls = []

            exit_code = run(
                [
                    "--description",
                    "Add Linux wrapper for AutoDev issue creation",
                    "--repo",
                    "owner/AutoDev",
                    "--create",
                    "--yes",
                    "--creation-log",
                    str(log_path),
                ],
                stdout=io.StringIO(),
                gh_runner=lambda command: calls.append(command) or "https://github.com/owner/AutoDev/issues/123",
                now=lambda: "2026-06-25T10:00:00Z",
            )

            self.assertEqual(exit_code, 0)
            self.assertEqual(calls[0][:4], ["gh", "issue", "create", "--repo"])
            record = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["created_issue_url"], "https://github.com/owner/AutoDev/issues/123")
            self.assertEqual(record["repository"], "owner/AutoDev")
            self.assertIn("source_hash", record)

    def test_create_skips_duplicate_source_description_from_creation_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "creation-log.jsonl"
            source = "Add duplicate detection to AutoDev issue creation"

            first = run(
                [
                    "--description",
                    source,
                    "--repo",
                    "owner/AutoDev",
                    "--create",
                    "--yes",
                    "--creation-log",
                    str(log_path),
                ],
                stdout=io.StringIO(),
                gh_runner=lambda command: "https://github.com/owner/AutoDev/issues/123",
                now=lambda: "2026-06-25T10:00:00Z",
            )
            second_output = io.StringIO()
            second = run(
                [
                    "--description",
                    source,
                    "--repo",
                    "owner/AutoDev",
                    "--create",
                    "--yes",
                    "--creation-log",
                    str(log_path),
                ],
                stdout=second_output,
                gh_runner=lambda command: self.fail(f"unexpected duplicate gh call: {command}"),
                now=lambda: "2026-06-25T10:01:00Z",
            )

            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertIn("Skipping duplicate description", second_output.getvalue())

    def test_create_refuses_more_than_max_issues_without_yes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            descriptions = Path(temp_dir) / "ideas.md"
            descriptions.write_text("Add first AutoDev issue creation idea\n---\nAdd second AutoDev issue creation idea\n", encoding="utf-8")
            output = io.StringIO()

            exit_code = run(
                [
                    "--description-file",
                    str(descriptions),
                    "--repo",
                    "owner/AutoDev",
                    "--create",
                    "--max-issues",
                    "1",
                ],
                stdout=output,
                gh_runner=lambda command: self.fail(f"unexpected gh call: {command}"),
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("Refusing to create 2 issues", output.getvalue())


if __name__ == "__main__":
    unittest.main()

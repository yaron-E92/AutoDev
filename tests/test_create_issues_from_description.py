import io
import json
import tempfile
import unittest
from pathlib import Path

from automation.create_issues_from_description import (
    draft_issues_with_model,
    run,
    select_repository,
    split_descriptions,
)


def model_response(title, context="AutoDev needs issue creation coverage.", labels=None):
    return json.dumps(
        {
            "issues": [
                {
                    "title": title,
                    "context": context,
                    "goal": title,
                    "scope": ["Keep the issue focused."],
                    "non_goals": ["Do not change the issue runner."],
                    "implementation_notes": ["Use the shared Python entry point."],
                    "acceptance_criteria": [f"{title} is covered."],
                    "labels": labels or ["automation"],
                }
            ]
        }
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

    def test_model_can_create_multiple_issue_drafts_from_one_description(self):
        drafts = draft_issues_with_model(
            "Add wrappers and docs for AutoDev issue creation.",
            model="coder-model",
            model_runner=lambda model, prompt: json.dumps(
                {
                    "issues": [
                        {
                            "title": "Add AutoDev issue creation wrappers",
                            "context": "AutoDev needs shell entry points for issue creation.",
                            "goal": "Add Linux and Windows wrappers.",
                            "scope": ["Create thin wrapper scripts."],
                            "non_goals": ["Do not change the issue runner."],
                            "implementation_notes": ["Forward arguments to the Python entry point."],
                            "acceptance_criteria": ["Linux and Windows wrappers call the Python tool."],
                            "labels": ["automation", "area:python"],
                        },
                        {
                            "title": "Document AutoDev issue creation usage",
                            "context": "Users need examples for the issue creation flow.",
                            "goal": "Document dry-run and create modes.",
                            "scope": ["Add cross-platform usage examples."],
                            "non_goals": ["Do not document unrelated automation flows."],
                            "implementation_notes": ["Cover repo-map inference."],
                            "acceptance_criteria": ["Docs include Linux and Windows examples."],
                            "labels": ["documentation"],
                        },
                    ]
                }
            ),
        )

        self.assertEqual(
            [draft.title for draft in drafts],
            [
                "Add AutoDev issue creation wrappers",
                "Document AutoDev issue creation usage",
            ],
        )
        self.assertIn("AutoDev needs shell entry points", drafts[0].body)
        self.assertIn("- [ ] Linux and Windows wrappers call the Python tool.", drafts[0].body)
        self.assertIn("documentation", drafts[1].labels)

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

    def test_default_dry_run_prints_model_generated_gh_command_without_creating_issue(self):
        output = io.StringIO()

        exit_code = run(
            ["--description", "Add Windows support to AutoDev", "--repo", "owner/AutoDev"],
            stdout=output,
            gh_runner=lambda command: self.fail(f"unexpected gh call: {command}"),
            model_runner=lambda model, prompt: model_response(
                "Add Windows support to AutoDev",
                context="AutoDev needs a Windows-friendly issue creation flow.",
            ),
        )

        self.assertEqual(exit_code, 0)
        value = output.getvalue()
        self.assertIn("Mode: dry-run", value)
        self.assertIn("Repository: owner/AutoDev", value)
        self.assertIn("AutoDev needs a Windows-friendly issue creation flow.", value)
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
                model_runner=lambda model, prompt: model_response("Add Linux wrapper for AutoDev issue creation"),
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
                model_runner=lambda model, prompt: model_response("Add duplicate detection to AutoDev issue creation"),
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
                model_runner=lambda model, prompt: model_response("Add duplicate detection to AutoDev issue creation"),
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
                model_runner=lambda model, prompt: self.fail(f"unexpected model call: {prompt}"),
            )

            self.assertEqual(exit_code, 2)
            self.assertIn("Refusing to create 2 issues", output.getvalue())


if __name__ == "__main__":
    unittest.main()
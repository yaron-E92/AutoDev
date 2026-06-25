import argparse
import io
import tempfile
import unittest
from pathlib import Path

from automation import run_real_issue


class RunRealIssueTests(unittest.TestCase):
    def test_issue_branch_name_uses_issue_number_and_slug(self):
        issue_text = "# GitHub Issue #18: Add cross-platform real-issue runner!\n"

        branch = run_real_issue.issue_branch_name(18, issue_text)

        self.assertEqual(branch, "codex/issue-18-add-cross-platform-real-issue-runner")

    def test_positive_int_rejects_non_positive_values(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            run_real_issue.positive_int("0")

    def test_build_run_summary_uses_routing_and_recommendations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            (out_dir / "routed-areas.json").write_text('{"areas":["ci","docs"]}', encoding="utf-8")
            (out_dir / "recommended-command-groups.json").write_text(
                '{"recommended_command_groups":["env","markdown-smoke"]}',
                encoding="utf-8",
            )

            summary = run_real_issue.build_run_summary(out_dir)

        self.assertIn("Routed areas: ci, docs", summary)
        self.assertIn("Recommended verification groups: env, markdown-smoke", summary)

    def test_fetch_issue_text_formats_gh_json(self):
        def fake_run(argv, *, cwd, stream, check=True, timeout=None):
            return run_real_issue.CommandResult(
                argv=argv,
                cwd=cwd,
                returncode=0,
                stdout=(
                    '{"title":"Fix runner","body":"Body text","url":"https://example.test/1",'
                    '"labels":[{"name":"codex:ready"},{"name":"area:python"}]}'
                ),
                stderr="",
            )

        original = run_real_issue.run_command
        try:
            run_real_issue.run_command = fake_run
            issue_text = run_real_issue.fetch_issue_text("owner/repo", 7, Path("."), stream=io.StringIO())
        finally:
            run_real_issue.run_command = original

        self.assertIn("# GitHub Issue #7: Fix runner", issue_text)
        self.assertIn("Labels: codex:ready, area:python", issue_text)
        self.assertIn("Body text", issue_text)


if __name__ == "__main__":
    unittest.main()

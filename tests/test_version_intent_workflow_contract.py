from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class VersionIntentWorkflowContractTests(unittest.TestCase):
    def test_reusable_workflow_fetches_current_authoritative_pr_body(self) -> None:
        text = (ROOT / ".github" / "workflows" / "version-intent.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pr_number:", text)
        self.assertIn("pull-requests: read", text)
        self.assertIn('gh api "repos/$GH_REPO/pulls/$PR_NUMBER"', text)
        self.assertIn("'.body // \"\"'", text)
        self.assertIn("pr-body: ${{ steps.pr.outputs.body }}", text)
        self.assertIn("FALLBACK_BODY: ${{ inputs.pr_body }}", text)

    def test_ci_retriggers_on_pr_body_edits_and_passes_pr_identity(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("- edited", text)
        self.assertIn(
            "pr_number: ${{ github.event.pull_request.number }}",
            text,
        )
        version_job = text.split("  version-intent:\n", 1)[1].split("\n  workflow-lint:", 1)[0]
        self.assertIn("pull-requests: read", version_job)
        self.assertIn(
            "github.event.action == 'edited' && 'intent' || 'source'",
            text,
        )
        for job in (
            "workflow-lint",
            "release-reproducibility",
            "version-policy-action",
            "python",
            "native-packaging",
            "bash-syntax",
            "powershell-syntax",
            "repository-hygiene",
        ):
            section = text.split(f"  {job}:\n", 1)[1].split("\n  ", 1)[0]
            self.assertIn("github.event.action != 'edited'", section, job)


if __name__ == "__main__":
    unittest.main()

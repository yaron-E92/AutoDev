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

    def test_pr_body_edits_use_isolated_version_intent_workflow(self) -> None:
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        intent = (ROOT / ".github" / "workflows" / "pr-version-intent.yml").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("- edited", ci)
        self.assertNotIn("github.event.action", ci)
        self.assertIn("- edited", intent)
        self.assertIn("uses: ./.github/workflows/version-intent.yml", intent)
        self.assertIn(
            "pr_number: ${{ github.event.pull_request.number }}",
            intent,
        )
        self.assertIn(
            "head: ${{ github.event.pull_request.head.sha }}",
            intent,
        )

    def test_source_ci_exposes_one_aggregate_gate(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("  ci-gate:\n", text)
        gate = text.split("  ci-gate:\n", 1)[1].split("\n  version-tag:", 1)[0]
        self.assertIn("name: CI gate", gate)
        self.assertIn("if: always()", gate)
        for job in (
            "version-intent",
            "workflow-lint",
            "release-reproducibility",
            "version-policy-action",
            "python",
            "native-packaging",
            "bash-syntax",
            "powershell-syntax",
            "repository-hygiene",
        ):
            self.assertIn(f"      - {job}\n", gate, job)

        version_tag = text.split("  version-tag:\n", 1)[1]
        self.assertIn("needs.ci-gate.result == 'success'", version_tag)
        self.assertIn("      - ci-gate\n", version_tag)


if __name__ == "__main__":
    unittest.main()

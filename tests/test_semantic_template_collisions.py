import unittest

from automation.semantic_contract import SemanticVerifierError
from automation.semantic_prompts import build_semantic_prompt


class SemanticTemplateCollisionTests(unittest.TestCase):
    def test_new_delimiter_renders_without_reprocessing_inserted_content(self):
        issue_text = (
            "# Issue\n"
            "Route: /campaigns/{{campaignId}}/npcs\n"
            "Legacy-looking literal: {{Plan}}\n"
            "New-looking literal: {~{Plan}~}\n"
        )

        prompt = build_semantic_prompt(
            issue_text=issue_text,
            synthesized_handoff="Handoff",
            plan="Actual implementation plan",
            changed_files=["src/a.py"],
            diff="diff --git a/src/a.py b/src/a.py",
            deterministic_evidence="checks passed",
            template="Issue:\n{~{IssueText}~}\nPlan:\n{~{Plan}~}\n",
        )

        self.assertIn("/campaigns/{{campaignId}}/npcs", prompt)
        self.assertIn("Legacy-looking literal: {{Plan}}", prompt)
        self.assertIn("New-looking literal: {~{Plan}~}", prompt)
        self.assertIn("Plan:\nActual implementation plan", prompt)

    def test_unknown_new_template_placeholder_is_rejected(self):
        with self.assertRaises(SemanticVerifierError) as raised:
            build_semantic_prompt(
                issue_text="# Issue",
                synthesized_handoff="Handoff",
                plan="Plan",
                changed_files=[],
                diff="",
                deterministic_evidence="checks passed",
                template=(
                    "Issue: {~{IssueText}~}\n"
                    "Missing: {~{MissingRequiredEvidence}~}\n"
                ),
            )

        self.assertEqual(
            raised.exception.classification,
            "unresolved_semantic_placeholders",
        )
        self.assertIn("MissingRequiredEvidence", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
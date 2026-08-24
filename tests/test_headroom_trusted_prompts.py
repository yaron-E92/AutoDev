import unittest
from pathlib import Path

from automation.headroom import compressible_ranges, infer_role


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "promptTemplates"


def render(name, values):
    value = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, replacement in values.items():
        value = value.replace("{~{" + key + "}~}", replacement)
    return value + "\n\nOutput contract:\nPROTECTED OUTPUT CONTRACT\n"


class HeadroomTrustedPromptTests(unittest.TestCase):
    def test_implementer_compresses_plan_only(self):
        prompt = render(
            "implementer.md",
            {
                "StackContext": "stack",
                "LocalCheck": "check",
                "Plan": "LONG PLANNER EVIDENCE",
                "IssueText": "EXACT ISSUE REQUIREMENT",
            },
        )

        ranges = compressible_ranges(prompt, "")
        evidence = "\n".join(prompt[start:end] for start, end in ranges)

        self.assertEqual(infer_role(prompt), "implementer")
        self.assertIn("LONG PLANNER EVIDENCE", evidence)
        self.assertNotIn("EXACT ISSUE REQUIREMENT", evidence)
        self.assertNotIn("PROTECTED OUTPUT CONTRACT", evidence)

    def test_local_repair_compresses_failure_log_only(self):
        prompt = render(
            "local-repair.md",
            {
                "StackContext": "stack",
                "LocalCheck": "check",
                "FailureLog": "LONG FAILURE LOG",
                "IssueText": "EXACT ISSUE REQUIREMENT",
            },
        )

        ranges = compressible_ranges(prompt, "")
        evidence = "\n".join(prompt[start:end] for start, end in ranges)

        self.assertEqual(infer_role(prompt), "fixer")
        self.assertIn("LONG FAILURE LOG", evidence)
        self.assertNotIn("EXACT ISSUE REQUIREMENT", evidence)
        self.assertNotIn("PROTECTED OUTPUT CONTRACT", evidence)

    def test_ci_repair_compresses_ci_and_plan_but_not_issue_or_contract(self):
        prompt = render(
            "ci-repair.md",
            {
                "StackContext": "stack",
                "LocalCheck": "check",
                "CiSummary": "LONG CI FAILURE EVIDENCE",
                "IssueText": "EXACT ISSUE REQUIREMENT",
                "Plan": "LONG PLANNER EVIDENCE",
            },
        )

        ranges = compressible_ranges(prompt, "")
        evidence = "\n".join(prompt[start:end] for start, end in ranges)

        self.assertEqual(infer_role(prompt), "fixer")
        self.assertIn("LONG CI FAILURE EVIDENCE", evidence)
        self.assertIn("LONG PLANNER EVIDENCE", evidence)
        self.assertNotIn("EXACT ISSUE REQUIREMENT", evidence)
        self.assertNotIn("PROTECTED OUTPUT CONTRACT", evidence)


if __name__ == "__main__":
    unittest.main()

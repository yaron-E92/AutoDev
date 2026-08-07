import unittest

from area_reader_v2.runner_core import (
    build_area_reader_prompt,
    build_coder_prompt,
    build_synthesis_prompt,
)
from automation.headroom import compressible_ranges, infer_role


class HeadroomAreaReaderTests(unittest.TestCase):
    def test_reader_compresses_bundle_but_preserves_original_issue(self):
        issue = "EXACT ORIGINAL ISSUE"
        prompt = build_area_reader_prompt(
            issue,
            "backend",
            "REPOSITORY MAP AND FILE BUNDLE",
            {"area": "backend", "included_files": ["src/a.py"]},
        )

        ranges = compressible_ranges(prompt, "reader")

        self.assertEqual(infer_role(prompt), "reader")
        self.assertEqual(len(ranges), 1)
        evidence = prompt[ranges[0][0]:ranges[0][1]]
        self.assertIn("REPOSITORY MAP AND FILE BUNDLE", evidence)
        self.assertNotIn(issue, evidence)
        self.assertIn(issue, prompt[:ranges[0][0]])

    def test_synthesizer_compresses_reader_evidence_but_preserves_issue(self):
        issue = "EXACT SYNTHESIS ISSUE"
        prompt = build_synthesis_prompt(
            issue,
            ["backend"],
            [
                {
                    "area": "backend",
                    "metadata": {"included_files": ["src/a.py"]},
                    "brief": "LONG AREA READER BRIEF",
                }
            ],
            {"solutions": ["App.sln"]},
            [{"name": "dotnet-solution", "recommended": True}],
        )

        ranges = compressible_ranges(prompt, "synthesizer")

        self.assertEqual(infer_role(prompt), "synthesizer")
        self.assertEqual(len(ranges), 1)
        evidence = prompt[ranges[0][0]:ranges[0][1]]
        self.assertIn("LONG AREA READER BRIEF", evidence)
        self.assertNotIn(issue, evidence)
        self.assertIn(issue, prompt[:ranges[0][0]])

    def test_area_planner_compresses_handoff_and_facts_but_preserves_issue(self):
        issue = "EXACT PLANNER ISSUE"
        prompt = build_coder_prompt(
            issue,
            "SYNTHESIZED HANDOFF",
            {"solutions": ["App.sln"]},
            {"recommended_command_groups": ["dotnet-solution"]},
            [{"name": "dotnet-solution"}],
        )

        ranges = compressible_ranges(prompt, "planner")

        self.assertEqual(infer_role(prompt), "planner")
        self.assertEqual(len(ranges), 1)
        evidence = prompt[ranges[0][0]:ranges[0][1]]
        self.assertIn("SYNTHESIZED HANDOFF", evidence)
        self.assertNotIn(issue, evidence)
        self.assertIn(issue, prompt[:ranges[0][0]])


if __name__ == "__main__":
    unittest.main()

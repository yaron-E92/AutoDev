import tempfile
import unittest
from pathlib import Path

from automation import planner_output


def six_section_plan(path="docs/architecture.md", action="Update the doc."):
    return f"""1) Where to look
- {path}
2) Files / areas likely to touch
- {path}
3) Assumptions
- Documentation only.
4) Plan
- {action}
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: edit {path}.
"""


class PlannerOutputTests(unittest.TestCase):
    def test_planner_stdout_is_written_to_plan_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / ".autodev-run" / "current" / "plan.md"

            plan = six_section_plan()
            planner_output.handle_planner_output(plan, plan_path)

            self.assertEqual(plan_path.read_text(encoding="utf-8"), plan)

    def test_planner_output_strips_thinking_preamble_when_final_plan_is_clear(self):
        output = """Thinking...
private scratch

1) Where to look
- src/App.xaml.cs
2) Files / areas likely to touch
- src/App.xaml.cs
3) Assumptions
- Small local fix.
4) Plan
- Inspect and patch the file.
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: patch the file.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            planner_output.handle_planner_output(output, plan_path)

            self.assertTrue(plan_path.read_text(encoding="utf-8").startswith("1) Where to look\n- src/App.xaml.cs\n"))
            self.assertEqual(plan_path.with_name("plan.md.raw").read_text(encoding="utf-8"), output)

    def test_planner_output_fails_when_preamble_cannot_be_safely_stripped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            with self.assertRaises(planner_output.PlannerOutputError) as raised:
                planner_output.handle_planner_output("Thinking...\nMaybe inspect files", plan_path)

            self.assertIn("raw response", str(raised.exception))
            self.assertTrue(plan_path.with_name("plan.md.raw").is_file())

    def test_planner_output_strips_control_chars_and_extracts_required_sections(self):
        output = "\x1b[?25lThinking...\b\b draft\r\n" """1) Where to look
- docs/architecture.md
2) Files / areas likely to touch
- docs/architecture.md
3) Assumptions
- Documentation only.
4) Plan
- Update the doc.
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: edit docs only.
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            planner_output.handle_planner_output(output, plan_path)

            plan = plan_path.read_text(encoding="utf-8")
            self.assertTrue(plan.startswith("1) Where to look"))
            self.assertNotIn("Thinking", plan)
            self.assertNotRegex(plan, r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

    def test_planner_output_extracts_last_complete_six_section_plan(self):
        output = (
            "Reasoning about an earlier draft.\n"
            + six_section_plan("docs/wrong.md", "Do the broad draft.")
            + "\nMore notes before the final answer.\n"
            + six_section_plan("docs/final.md", "Apply the narrowed change.")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            planner_output.handle_planner_output(output, plan_path)

            plan = plan_path.read_text(encoding="utf-8")
            self.assertIn("docs/final.md", plan)
            self.assertNotIn("docs/wrong.md", plan)

    def test_planner_output_rejects_contaminated_final_plan(self):
        output = six_section_plan("docs/architecture.md", "Wait, let's refine this before editing.")
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            with self.assertRaises(planner_output.PlannerOutputError):
                planner_output.handle_planner_output(output, plan_path)

            self.assertTrue(plan_path.with_name("plan.md.raw").is_file())
            self.assertTrue(plan_path.with_name("plan.md.parser-error.md").is_file())
            self.assertFalse(plan_path.exists())

    def test_planner_output_rejects_reasoning_after_extracted_plan(self):
        output = """Intro
1) Where to look
- docs/architecture.md
2) Files / areas likely to touch
- docs/architecture.md
3) Assumptions
- Documentation only.
4) Plan
- Update the doc.
5) Risks / gotchas
- Keep scope narrow.
6) Recommended implementation approach
- Option A: edit docs only.
Thinking... hidden scratchpad
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            plan_path = Path(temp_dir) / "plan.md"

            with self.assertRaises(planner_output.PlannerOutputError):
                planner_output.handle_planner_output(output, plan_path)

            self.assertTrue(plan_path.with_name("plan.md.raw").is_file())
            self.assertTrue(plan_path.with_name("plan.md.parser-error.md").is_file())
            self.assertFalse(plan_path.exists())


if __name__ == "__main__":
    unittest.main()

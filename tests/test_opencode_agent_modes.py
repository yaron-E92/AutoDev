from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = REPO_ROOT / "integrations" / "opencode" / "agents"
ROLE_AGENTS = (
    "reader",
    "synthesizer",
    "planner",
    "implementer",
    "fixer",
    "verifier",
)


class OpenCodeAgentModeTests(unittest.TestCase):
    def test_python_coordinated_roles_are_directly_selectable_and_cannot_delegate(self):
        for role in ROLE_AGENTS:
            text = (AGENT_ROOT / f"autodev-{role}.md").read_text(encoding="utf-8")
            frontmatter = text.split("---", 2)[1]
            self.assertIn("mode: all", frontmatter, role)
            self.assertNotIn("mode: subagent", frontmatter, role)
            self.assertIn("task: deny", frontmatter, role)

    def test_legacy_coordinator_mode_is_not_changed_by_direct_role_fix(self):
        text = (AGENT_ROOT / "autodev-coordinator.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        self.assertNotIn("mode: all", frontmatter)


if __name__ == "__main__":
    unittest.main()

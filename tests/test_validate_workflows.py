from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import validate_workflows


class ValidateWorkflowsTests(unittest.TestCase):
    def test_self_repository_dollar_reference_is_allowed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text(
                "jobs:\n"
                "  local-action:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - uses: $/.github/actions/version-policy\n"
                "  local-workflow:\n"
                "    uses: $/.github/workflows/version-tag.yml\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_workflows.validate_action_refs(repo), [])

    def test_external_mutable_reference_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci.yml").write_text(
                "jobs:\n"
                "  bad:\n"
                "    uses: someone/repo/.github/workflows/example.yml@main\n",
                encoding="utf-8",
            )
            errors = validate_workflows.validate_action_refs(repo)
            self.assertEqual(len(errors), 1)
            self.assertIn("full 40-character commit SHA", errors[0])


if __name__ == "__main__":
    unittest.main()

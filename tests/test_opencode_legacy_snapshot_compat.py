import json
import tempfile
import unittest
from pathlib import Path

from automation import opencode_runtime, workflow_stages


class OpenCodeLegacySnapshotCompatibilityTests(unittest.TestCase):
    def test_pre_83_snapshot_uses_current_opencode_ignore_rules_without_rewrite(self):
        original_ignored = workflow_stages.ignored_workspace_path
        original_read_json = workflow_stages.read_json
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                repo = Path(temp_dir)
                current = repo / workflow_stages.CURRENT_DIR
                current.mkdir(parents=True)
                (repo / "src").mkdir()
                (repo / "src" / "product.cs").write_text("stable\n", encoding="utf-8")
                (repo / "src" / "delete.cs").write_text("delete me\n", encoding="utf-8")
                (repo / "opencode.jsonc").write_text('{"agent": {}}\n', encoding="utf-8")

                baseline_path = current / "workspace-snapshot.json"
                workflow_stages.write_workspace_snapshot(repo, baseline_path)
                baseline_bytes = baseline_path.read_bytes()
                baseline = json.loads(baseline_bytes)

                self.assertIn("opencode.jsonc", baseline)

                state = {"BaseSha": "prepared-base"}
                opencode_runtime.install_workflow_guards()

                self.assertEqual(workflow_stages.workspace_changes(repo, current, state), [])
                first_identity = workflow_stages.source_identity(repo, current, state)["identity"]

                (repo / "opencode.jsonc").write_text('{"agent": {"changed": true}}\n', encoding="utf-8")
                self.assertEqual(workflow_stages.workspace_changes(repo, current, state), [])
                second_identity = workflow_stages.source_identity(repo, current, state)["identity"]
                self.assertEqual(first_identity, second_identity)

                (repo / "src" / "product.cs").write_text("modified\n", encoding="utf-8")
                (repo / "src" / "delete.cs").unlink()
                (repo / "src" / "added.cs").write_text("added\n", encoding="utf-8")

                changes = workflow_stages.workspace_changes(repo, current, state)
                self.assertEqual(
                    changes,
                    [
                        {"Path": "src/added.cs", "Status": "added"},
                        {"Path": "src/delete.cs", "Status": "deleted"},
                        {"Path": "src/product.cs", "Status": "modified"},
                    ],
                )
                third_identity = workflow_stages.source_identity(repo, current, state)["identity"]
                self.assertNotEqual(first_identity, third_identity)

                self.assertEqual(baseline_path.read_bytes(), baseline_bytes)
        finally:
            workflow_stages.ignored_workspace_path = original_ignored
            workflow_stages.read_json = original_read_json


if __name__ == "__main__":
    unittest.main()

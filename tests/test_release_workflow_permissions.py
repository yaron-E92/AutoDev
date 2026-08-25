from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowPermissionTests(unittest.TestCase):
    def test_release_reusable_ci_allows_nested_version_tag_permissions(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        release_ci_job = release.split("  ci:\n", 1)[1].split("\n  publish:\n", 1)[0]
        version_tag_job = ci.split("  version-tag:\n", 1)[1]

        self.assertIn("contents: write", version_tag_job)
        self.assertIn("pull-requests: read", version_tag_job)
        self.assertIn("contents: write", release_ci_job)
        self.assertIn("pull-requests: read", release_ci_job)

    def test_generated_notes_start_at_previous_published_release(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        publish_step = release.split("      - name: Create release or prove identical idempotent rerun\n", 1)[1]

        self.assertIn("gh release list", publish_step)
        self.assertIn("--exclude-drafts", publish_step)
        self.assertIn("--order desc", publish_step)
        self.assertIn("--limit 1", publish_step)
        self.assertIn("--json tagName", publish_step)
        self.assertIn('notes_start_args=(--notes-start-tag "$previous_release_tag")', publish_step)
        self.assertIn('"${notes_start_args[@]}"', publish_step)
        self.assertNotIn("git describe", publish_step)
        self.assertLess(publish_step.index("gh release list"), publish_step.index("gh release create"))


if __name__ == "__main__":
    unittest.main()

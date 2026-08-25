from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowPermissionTests(unittest.TestCase):
    def test_release_reusable_ci_allows_nested_version_tag_permissions(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        release_ci_job = release.split("  ci:\n", 1)[1].split("\n  native-release:\n", 1)[0]
        version_tag_job = ci.split("  version-tag:\n", 1)[1]

        self.assertIn("contents: write", version_tag_job)
        self.assertIn("pull-requests: read", version_tag_job)
        self.assertIn("contents: write", release_ci_job)
        self.assertIn("pull-requests: read", release_ci_job)

    def test_native_workflow_separates_verification_from_release_packaging(self) -> None:
        native = (ROOT / ".github" / "workflows" / "native-packaging.yml").read_text(encoding="utf-8")
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn("release_packaging:", native)
        self.assertIn("type: boolean", native)
        self.assertIn("default: false", native)
        self.assertIn("windows-verify:", native)
        self.assertIn("linux-verify:", native)
        self.assertIn("windows-release:", native)
        self.assertIn("linux-release:", native)
        self.assertIn("if: ${{ inputs.release_packaging == false }}", native)
        self.assertIn("if: ${{ inputs.release_packaging == true }}", native)

        release_native_job = release.split("  native-release:\n", 1)[1].split("\n  sign-windows:\n", 1)[0]
        self.assertIn("needs: ci", release_native_job)
        self.assertIn("uses: ./.github/workflows/native-packaging.yml", release_native_job)
        self.assertIn("version: ${{ inputs.tag }}", release_native_job)
        self.assertIn("release_packaging: true", release_native_job)

    def test_windows_signing_secrets_exist_only_on_manual_release_boundary(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        native = (ROOT / ".github" / "workflows" / "native-packaging.yml").read_text(encoding="utf-8")

        signing_job = release.split("  sign-windows:\n", 1)[1].split("\n  publish:\n", 1)[0]
        publish_job = release.split("  publish:\n", 1)[1]

        self.assertIn("runs-on: windows-latest", signing_job)
        self.assertIn("AUTODEV_WINDOWS_SIGNING_PFX_BASE64", signing_job)
        self.assertIn("AUTODEV_WINDOWS_SIGNING_PFX_PASSWORD", signing_job)
        self.assertIn("./automation/sign_windows_release.ps1", signing_job)
        self.assertIn("autodev-native-windows-signed", signing_job)
        self.assertIn("autodev-native-windows-signed", publish_job)
        self.assertNotIn("AUTODEV_WINDOWS_SIGNING_PFX_BASE64", ci)
        self.assertNotIn("AUTODEV_WINDOWS_SIGNING_PFX_PASSWORD", ci)
        self.assertNotIn("AUTODEV_WINDOWS_SIGNING_PFX_BASE64", native)
        self.assertNotIn("AUTODEV_WINDOWS_SIGNING_PFX_PASSWORD", native)

    def test_release_rerun_branches_before_downloading_or_signing_msi(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        signing_job = release.split("  sign-windows:\n", 1)[1].split("\n  publish:\n", 1)[0]

        self.assertIn("Resolve new-release or idempotent-rerun signing path", signing_job)
        self.assertIn("Download prepared unsigned MSI for a new release", signing_job)
        self.assertIn("Download existing signed MSI for an idempotent rerun", signing_job)
        self.assertIn("if: steps.signed-input.outputs.reuse != 'true'", signing_job)
        self.assertIn("if: steps.signed-input.outputs.reuse == 'true'", signing_job)
        self.assertIn("gh release download", signing_job)
        self.assertIn("-VerifyOnly", signing_job)
        self.assertIn("reuse=true", signing_job)

        decision = signing_job.index("Resolve new-release or idempotent-rerun signing path")
        unsigned_download = signing_job.index("Download prepared unsigned MSI for a new release")
        reused_download = signing_job.index("Download existing signed MSI for an idempotent rerun")
        self.assertLess(decision, unsigned_download)
        self.assertLess(decision, reused_download)

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

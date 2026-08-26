from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from automation import package_release


ROOT = Path(__file__).resolve().parents[1]


class ArtifactContractTests(unittest.TestCase):
    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        )
        return completed.stdout.strip()

    def _repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "autodev@example.invalid")
        self._git(repo, "config", "user.name", "AutoDev Tests")
        files = {
            "automation/example.py": "print('common')\n",
            "windows/scripts/example.ps1": "Write-Output 'windows'\n",
            "LICENSE": "SPDX-License-Identifier: GPL-3.0-only\n",
        }
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "fixture")
        return temp, repo, self._git(repo, "rev-parse", "HEAD")

    def test_artifacts_is_top_level_ignored_generated_boundary(self) -> None:
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("artifacts/", ignored)

    def test_release_output_dir_uses_canonical_target_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            self.assertEqual(
                package_release.release_output_dir(repo),
                repo.resolve() / "artifacts" / "release" / "publish",
            )
            self.assertEqual(
                package_release.release_output_dir(repo, "windows-signed"),
                repo.resolve() / "artifacts" / "release" / "windows-signed",
            )
            with self.assertRaises(package_release.ReleasePackagingError):
                package_release.release_output_dir(repo, "../outside")

    def test_default_packaging_handoff_regenerates_after_artifacts_is_deleted(self) -> None:
        temp, repo, commit = self._repo()
        with temp:
            args = [
                "--repo",
                str(repo),
                "--version",
                "v1.2.3",
                "--commit",
                commit,
            ]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(package_release.main(args), 0)

            output = repo / "artifacts" / "release" / "publish"
            expected = {
                path.name: path.read_bytes()
                for path in output.iterdir()
                if path.is_file()
            }
            self.assertIn("autodev-v1.2.3-common.zip", expected)
            self.assertIn("autodev-v1.2.3-windows.zip", expected)
            self.assertIn("autodev-release-manifest.json", expected)
            self.assertIn("SHA256SUMS", expected)

            shutil.rmtree(repo / "artifacts")
            self.assertFalse(output.exists())

            with redirect_stdout(io.StringIO()):
                self.assertEqual(package_release.main(args), 0)
            regenerated = {
                path.name: path.read_bytes()
                for path in output.iterdir()
                if path.is_file()
            }
            self.assertEqual(regenerated, expected)

    def test_release_workflows_use_artifacts_for_publishable_handoffs(self) -> None:
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        native = (ROOT / ".github" / "workflows" / "native-packaging.yml").read_text(encoding="utf-8")

        self.assertIn("artifacts/release/windows-unsigned", release)
        self.assertIn("artifacts/release/windows", release)
        self.assertIn("artifacts/release/native", release)
        self.assertIn("--target publish", release)
        self.assertIn("artifacts/release/publish/SHA256SUMS", release)
        self.assertIn('gh release create "$RELEASE_TAG" artifacts/release/publish/*', release)
        self.assertNotIn("--out dist", release)
        self.assertNotIn("dist/SHA256SUMS", release)
        self.assertNotIn("dist/*", release)

        windows_release = native.split("  windows-release:\n", 1)[1].split("\n  linux-release:\n", 1)[0]
        linux_release = native.split("  linux-release:\n", 1)[1]
        self.assertIn('--out "artifacts/release/windows-unsigned"', windows_release)
        self.assertIn("path: artifacts/release/windows-unsigned/*.msi", windows_release)
        self.assertIn('--out "artifacts/release/linux"', linux_release)
        self.assertIn("artifacts/release/linux/*.deb", linux_release)
        self.assertIn("artifacts/release/linux/*.rpm", linux_release)

    def test_release_docs_define_disposable_artifact_handoff(self) -> None:
        docs = (ROOT / "docs" / "releases.md").read_text(encoding="utf-8")
        self.assertIn("`artifacts/` is AutoDev's top-level generated-output boundary", docs)
        self.assertIn("artifacts/release/<target>/", docs)
        self.assertIn("artifacts/release/publish/", docs)
        self.assertIn("`artifacts/` is disposable generated state", docs)
        self.assertIn("rerunning the same exact-source packaging commands recreates", docs)


if __name__ == "__main__":
    unittest.main()

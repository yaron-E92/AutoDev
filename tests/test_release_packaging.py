from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from automation import package_release, validate_workflows


class ReleasePackagingTests(unittest.TestCase):
    def _git(self, repo: Path, *args: str) -> str:
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
            "docs/installation.md": "# Install AutoDev\n",
            "windows/scripts/example.ps1": "Write-Output 'windows'\n",
        }
        for relative, content in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "fixture")
        return temp, repo, self._git(repo, "rev-parse", "HEAD")

    def test_release_bundles_are_reproducible_and_tied_to_commit_bytes(self) -> None:
        temp, repo, commit = self._repo()
        with temp:
            out_a = repo / "dist-a"
            out_b = repo / "dist-b"
            first = package_release.build_release(repo, out_a, "v1.2.3", commit)
            (repo / "automation/example.py").write_text("tampered working tree\n", encoding="utf-8")
            second = package_release.build_release(repo, out_b, "v1.2.3", commit)

            self.assertEqual(first, second)
            self.assertEqual(first["commit_sha"], commit)
            for name in [
                "autodev-v1.2.3-common.zip",
                "autodev-v1.2.3-windows.zip",
                "autodev-release-manifest.json",
                "SHA256SUMS",
            ]:
                self.assertEqual((out_a / name).read_bytes(), (out_b / name).read_bytes())

            with zipfile.ZipFile(out_b / "autodev-v1.2.3-common.zip") as archive:
                self.assertEqual(
                    archive.read("automation/example.py"),
                    b"print('common')\n",
                )
                self.assertEqual(
                    archive.read("docs/installation.md"),
                    b"# Install AutoDev\n",
                )

    def test_manifest_lists_file_and_archive_hashes(self) -> None:
        temp, repo, commit = self._repo()
        with temp:
            out = repo / "dist"
            package_release.build_release(repo, out, "v2.0.0", commit)
            manifest = json.loads((out / "autodev-release-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], "v2.0.0")
            self.assertEqual(manifest["commit_sha"], commit)
            self.assertEqual(set(manifest["bundles"]), {"common", "windows"})
            common_files = manifest["bundles"]["common"]["files"]
            common_paths = {entry["path"] for entry in common_files}
            self.assertIn("automation/example.py", common_paths)
            self.assertIn("docs/installation.md", common_paths)
            automation_entry = next(
                entry for entry in common_files if entry["path"] == "automation/example.py"
            )
            self.assertEqual(len(automation_entry["sha256"]), 64)
            self.assertEqual(len(manifest["bundles"]["common"]["sha256"]), 64)

    def test_requested_commit_must_match_checkout(self) -> None:
        temp, repo, _ = self._repo()
        with temp:
            with self.assertRaises(package_release.ReleasePackagingError):
                package_release.build_release(repo, repo / "dist", "v1.0.0", "0" * 40)


class WorkflowReferenceTests(unittest.TestCase):
    def test_external_action_refs_require_full_commit_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            workflows = repo / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "bad.yml").write_text(
                "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v7\n",
                encoding="utf-8",
            )
            errors = validate_workflows.validate_action_refs(repo)
            self.assertEqual(len(errors), 1)
            self.assertIn("full 40-character commit SHA", errors[0])

            (workflows / "bad.yml").write_text(
                "jobs:\n  test:\n    steps:\n"
                "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
                "      - uses: ./.github/actions/local\n",
                encoding="utf-8",
            )
            self.assertEqual(validate_workflows.validate_action_refs(repo), [])


if __name__ == "__main__":
    unittest.main()

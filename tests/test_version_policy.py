from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import version_policy


class VersionPolicyTests(unittest.TestCase):
    def test_exact_intent_requires_one_directive(self):
        self.assertEqual(version_policy.parse_exact_intent("text\n+semver: minor\n"), "minor")
        with self.assertRaises(version_policy.VersionPolicyError):
            version_policy.parse_exact_intent("no directive")
        with self.assertRaises(version_policy.VersionPolicyError):
            version_policy.parse_exact_intent("+semver: patch\n+semver: patch\n")
        with self.assertRaises(version_policy.VersionPolicyError):
            version_policy.parse_exact_intent("+semver: patch\n+semver: major\n")

    def test_highest_bump_is_deterministic(self):
        self.assertEqual(version_policy.highest_bump([]), "none")
        self.assertEqual(version_policy.highest_bump(["none", "patch"]), "patch")
        self.assertEqual(version_policy.highest_bump(["patch", "minor", "none"]), "minor")
        self.assertEqual(version_policy.highest_bump(["minor", "major", "patch"]), "major")

    def test_version_bumps_reset_lower_components(self):
        version = version_policy.Version(1, 2, 3)
        self.assertEqual(version.bump("patch").semver, "1.2.4")
        self.assertEqual(version.bump("minor").semver, "1.3.0")
        self.assertEqual(version.bump("major").semver, "2.0.0")
        self.assertEqual(version.bump("none").semver, "1.2.3")

    def test_pr_candidate_uses_latest_reachable_canonical_tag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _ = self._repo(Path(temp_dir))
            self._commit(repo, "base")
            self._git(repo, "tag", "-a", "v1.2.3", "-m", "base version")
            self._commit(repo, "change")

            resolution = version_policy.candidate_for_pr(
                repo,
                "## change\n\n+semver: minor\n",
            )

            self.assertEqual(resolution.base_tag, "v1.2.3")
            self.assertEqual(resolution.bump, "minor")
            self.assertEqual(resolution.version.semver, "1.3.0")
            self.assertTrue(resolution.tag_required)

    def test_main_resolution_uses_highest_explicit_merged_pr_intent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _ = self._repo(Path(temp_dir))
            base = self._commit(repo, "base")
            self._git(repo, "tag", "-a", "v1.0.0", base, "-m", "base")
            patch_sha = self._commit(repo, "patch change")
            minor_sha = self._commit(repo, "minor change")
            self._git(repo, "push", "origin", "main", "--tags")

            pulls = {
                patch_sha: [self._pull(11, "+semver: patch")],
                minor_sha: [self._pull(12, "+semver: minor")],
            }
            runner = self._runner_with_pulls(pulls)
            resolution = version_policy.resolve_main(
                repo,
                repository="owner/repo",
                head=minor_sha,
                runner=runner,
            )

            self.assertFalse(resolution.superseded)
            self.assertEqual(resolution.base_tag, "v1.0.0")
            self.assertEqual(resolution.intents, ("patch", "minor"))
            self.assertEqual(resolution.bump, "minor")
            self.assertEqual(resolution.version.tag, "v1.1.0")

    def test_none_does_not_create_a_tag(self):
        resolution = version_policy.Resolution(
            base_tag="v1.2.3",
            base_version=version_policy.Version(1, 2, 3),
            bump="none",
            version=version_policy.Version(1, 2, 3),
            source_sha="abc",
            intents=("none",),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(
                version_policy.create_annotated_tag(Path(temp_dir), resolution),
                "no-tag",
            )

    def test_tag_creation_is_annotated_idempotent_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, bare = self._repo(Path(temp_dir))
            sha = self._commit(repo, "change")
            self._git(repo, "push", "origin", "main")
            resolution = version_policy.Resolution(
                base_tag="v1.0.0",
                base_version=version_policy.Version(1, 0, 0),
                bump="patch",
                version=version_policy.Version(1, 0, 1),
                source_sha=sha,
                intents=("patch",),
            )

            self.assertEqual(version_policy.create_annotated_tag(repo, resolution), "created")
            self.assertEqual(version_policy.create_annotated_tag(repo, resolution), "already-exists")
            self.assertEqual(self._git(repo, "cat-file", "-t", "v1.0.1"), "tag")
            self.assertEqual(self._git(repo, "rev-list", "-n", "1", "v1.0.1"), sha)
            remote_tags = subprocess.run(
                ["git", "--git-dir", str(bare), "tag", "--list"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.splitlines()
            self.assertIn("v1.0.1", remote_tags)

    def test_superseded_main_sha_is_not_tagged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _ = self._repo(Path(temp_dir))
            old_sha = self._commit(repo, "old")
            self._git(repo, "push", "origin", "main")
            self._commit(repo, "new")
            self._git(repo, "push", "origin", "main")

            resolution = version_policy.resolve_main(
                repo,
                repository="owner/repo",
                head=old_sha,
                runner=self._runner_with_pulls({}),
            )
            self.assertTrue(resolution.superseded)
            self.assertFalse(resolution.tag_required)

    @staticmethod
    def _pull(number: int, body: str) -> dict[str, object]:
        return {
            "number": number,
            "body": body,
            "merged_at": "2026-08-19T00:00:00Z",
            "base": {"ref": "main"},
        }

    @staticmethod
    def _runner_with_pulls(pulls: dict[str, list[dict[str, object]]]):
        def runner(command, **kwargs):
            if command and command[0] == "gh":
                commit = str(command[-1]).split("/")[-2]
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(pulls.get(commit, [])),
                    stderr="",
                )
            return subprocess.run(command, **kwargs)

        return runner

    def _repo(self, root: Path) -> tuple[Path, Path]:
        bare = root / "remote.git"
        repo = root / "repo"
        subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        self._git(repo, "config", "user.name", "AutoDev Tests")
        self._git(repo, "config", "user.email", "autodev-tests@example.invalid")
        self._git(repo, "remote", "add", "origin", str(bare))
        return repo, bare

    def _commit(self, repo: Path, message: str) -> str:
        marker = repo / "marker.txt"
        previous = marker.read_text(encoding="utf-8") if marker.exists() else ""
        marker.write_text(previous + message + "\n", encoding="utf-8")
        self._git(repo, "add", "marker.txt")
        self._git(repo, "commit", "-m", message)
        return self._git(repo, "rev-parse", "HEAD")

    @staticmethod
    def _git(repo: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=True,
        )
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()

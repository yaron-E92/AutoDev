from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import repository_identity


class RepositoryIdentityTests(unittest.TestCase):
    def _repo(self, root: Path, *, configured: str = "") -> Path:
        repo = root / "target"
        repo.mkdir()
        (repo / ".git").mkdir()
        if configured:
            config = repo / ".autodev" / "repo.json"
            config.parent.mkdir()
            config.write_text(
                json.dumps({"version": 1, "github_repository": configured}) + "\n",
                encoding="utf-8",
            )
        return repo

    def _remote_runner(self, url: str, calls: list[tuple[list[str], Path]]):
        def runner(argv, **kwargs):
            calls.append((list(argv), Path(kwargs["cwd"])))
            return SimpleNamespace(returncode=0, stdout=url + "\n", stderr="")

        return runner

    def test_https_and_ssh_github_remotes_resolve_without_environment(self):
        cases = (
            ("https://github.com/Tax-Technology/goldilocks.git", "Tax-Technology/goldilocks"),
            ("git@github.com:Tax-Technology/goldilocks.git", "Tax-Technology/goldilocks"),
            ("ssh://git@github.com/Tax-Technology/goldilocks.git", "Tax-Technology/goldilocks"),
        )
        for remote_url, expected in cases:
            with self.subTest(remote_url=remote_url), tempfile.TemporaryDirectory() as temp_dir:
                repo = self._repo(Path(temp_dir))
                calls: list[tuple[list[str], Path]] = []
                actual = repository_identity.resolve_github_repository(
                    repo,
                    env={},
                    runner=self._remote_runner(remote_url, calls),
                )

                self.assertEqual(actual, expected)
                self.assertEqual(calls[0][0], ["git", "remote", "get-url", "--all", "origin"])
                self.assertEqual(calls[0][1], repo.resolve())

    def test_explicit_owner_or_repo_environment_override_wins_over_remote(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            calls: list[tuple[list[str], Path]] = []
            runner = self._remote_runner("https://github.com/derived/project.git", calls)

            owner_override = repository_identity.resolve_github_repository(
                repo,
                env={"GITHUB_OWNER": "explicit-owner"},
                runner=runner,
            )
            repo_override = repository_identity.resolve_github_repository(
                repo,
                env={"GITHUB_REPO": "explicit-repo"},
                runner=runner,
            )
            full_override = repository_identity.resolve_github_repository(
                repo,
                explicit="full/override",
                env={},
                runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote must not run")),
            )

            self.assertEqual(owner_override, "explicit-owner/project")
            self.assertEqual(repo_override, "derived/explicit-repo")
            self.assertEqual(full_override, "full/override")

    def test_committed_repository_config_precedes_remote(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir), configured="configured/repository")

            actual = repository_identity.resolve_github_repository(
                repo,
                env={},
                runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote must not run")),
            )

            self.assertEqual(actual, "configured/repository")

    def test_non_default_remote_name_is_honored_for_target_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = self._repo(root)
            calls: list[tuple[list[str], Path]] = []

            actual = repository_identity.resolve_github_repository(
                repo,
                env={"REMOTE_NAME": "upstream"},
                runner=self._remote_runner("https://github.com/owner/target.git", calls),
            )

            self.assertEqual(actual, "owner/target")
            self.assertEqual(calls, [(["git", "remote", "get-url", "--all", "upstream"], repo.resolve())])

    def test_non_github_malformed_and_ambiguous_remotes_fail_actionably(self):
        cases = (
            "https://gitlab.com/owner/repo.git\n",
            "not-a-remote\n",
            "https://github.com/one/repo.git\nhttps://github.com/two/repo.git\n",
        )
        for stdout in cases:
            with self.subTest(stdout=stdout), tempfile.TemporaryDirectory() as temp_dir:
                repo = self._repo(Path(temp_dir))

                def runner(_argv, **_kwargs):
                    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

                with self.assertRaises(repository_identity.RepositoryIdentityError) as raised:
                    repository_identity.resolve_github_repository(repo, env={}, runner=runner)

                message = str(raised.exception)
                self.assertIn("Git remote 'origin'", message)
                self.assertTrue("GitHub" in message or "multiple" in message)

    def test_missing_remote_failure_explains_all_supported_identity_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))

            def runner(_argv, **_kwargs):
                return SimpleNamespace(returncode=2, stdout="", stderr="missing remote")

            with self.assertRaises(repository_identity.RepositoryIdentityError) as raised:
                repository_identity.resolve_github_repository(repo, env={}, runner=runner)

            message = str(raised.exception)
            self.assertIn("GITHUB_OWNER/GITHUB_REPO", message)
            self.assertIn("github_repository", message)
            self.assertIn("REMOTE_NAME", message)
            self.assertIn(str(repo.resolve()), message)

    def test_legacy_queue_fallback_remains_available_when_remote_is_absent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            calls: list[list[str]] = []

            def runner(argv, **_kwargs):
                args = list(argv)
                calls.append(args)
                if args[:2] == ["git", "remote"]:
                    return SimpleNamespace(returncode=2, stdout="", stderr="missing")
                if args[:3] == ["gh", "repo", "view"]:
                    return SimpleNamespace(returncode=0, stdout="legacy/repository\n", stderr="")
                raise AssertionError(args)

            actual = repository_identity.resolve_github_repository(
                repo,
                env={},
                runner=runner,
                allow_gh_fallback=True,
            )

            self.assertEqual(actual, "legacy/repository")
            self.assertEqual(calls[0], ["git", "remote", "get-url", "--all", "origin"])
            self.assertEqual(calls[1][:3], ["gh", "repo", "view"])


if __name__ == "__main__":
    unittest.main()

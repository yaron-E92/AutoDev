from __future__ import annotations

from automation import queue_contract

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import repo_setup


REPO_ROOT = Path(__file__).resolve().parents[1]


class RepoSetupGitHub:
    def __init__(self):
        self.repo = "owner/repo"
        self.labels: dict[str, tuple[str, str]] = {
            "unrelated": ("ffffff", "keep me"),
        }
        self.calls: list[list[str]] = []
        self.mutations: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        args = list(argv)
        self.calls.append(args)
        if args[0] != "gh":
            raise AssertionError(f"unexpected non-GitHub command: {args}")
        command = args[1:]
        if command[:2] == ["repo", "view"]:
            return SimpleNamespace(returncode=0, stdout=self.repo + "\n", stderr="")
        if command[:2] == ["label", "list"]:
            payload = [
                {"name": name, "color": color, "description": description}
                for name, (color, description) in sorted(self.labels.items())
            ]
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if command[:2] == ["label", "create"]:
            self.mutations.append(args)
            name = command[2]
            color = command[command.index("--color") + 1]
            description = command[command.index("--description") + 1]
            self.labels[name] = (color.casefold(), description)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:2] == ["label", "edit"]:
            self.mutations.append(args)
            name = command[2]
            color = command[command.index("--color") + 1]
            description = command[command.index("--description") + 1]
            self.labels[name] = (color.casefold(), description)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected GitHub command: {args}")


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


class RepoSetupTests(unittest.TestCase):
    def test_repo_install_creates_only_autodev_policy_and_labels_without_managing_issues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(Path(temp_dir))
            fake = RepoSetupGitHub()

            first = repo_setup.install_repo(
                repo,
                github_repo=fake.repo,
                enable_opencode=False,
                runner=fake,
            )
            mutation_count = len(fake.mutations)
            second = repo_setup.install_repo(
                repo,
                github_repo=fake.repo,
                enable_opencode=False,
                runner=fake,
            )

            self.assertTrue((repo / ".autodev" / "repo.json").is_file())
            self.assertTrue((repo / ".autodev" / "queue.json").is_file())
            self.assertTrue((repo / ".autodev" / "roadmap.yaml").is_file())
            self.assertTrue((repo / ".autodev" / "privacy.json").is_file())
            self.assertFalse((repo / ".opencode").exists())
            self.assertEqual(first.github_repository, fake.repo)
            self.assertEqual(second.created, ())
            self.assertEqual(len(fake.mutations), mutation_count)
            self.assertEqual(fake.labels["unrelated"], ("ffffff", "keep me"))
            self.assertTrue(
                all(name in fake.labels for name in queue_contract.LABEL_SPECS)
            )
            self.assertFalse(any(call[1:3] == ["issue", "edit"] for call in fake.calls))

    def test_doctor_is_healthy_for_installed_non_opencode_repo_and_reports_grants_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(Path(temp_dir))
            fake = RepoSetupGitHub()
            repo_setup.install_repo(
                repo,
                github_repo=fake.repo,
                enable_opencode=False,
                runner=fake,
            )
            with patch.dict(
                os.environ,
                {"GITHUB_OWNER": "owner", "GITHUB_REPO": "repo"},
                clear=False,
            ):
                result = repo_setup.doctor(
                    repo,
                    github_repo=fake.repo,
                    runner=fake,
                    which=lambda name: f"/tools/{name}",
                )

            self.assertTrue(result.healthy)
            checks = {item.name: item for item in result.checks}
            self.assertEqual(checks["cli"].state, "ok")
            self.assertIn("canonical CLI root:", checks["cli"].detail)
            self.assertEqual(checks["queue-labels"].state, "ok")
            self.assertEqual(checks["opencode-assets"].state, "info")
            self.assertIn("active=0", checks["privacy-grants"].detail)
            self.assertNotIn("route_identities", checks["privacy-grants"].detail)

    def test_doctor_accepts_frozen_native_cli_without_source_checkout_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = make_repo(root)
            fake = RepoSetupGitHub()
            repo_setup.install_repo(
                repo,
                github_repo=fake.repo,
                enable_opencode=False,
                runner=fake,
            )
            native_root = root / "opt" / "autodev"
            native_root.mkdir(parents=True)
            executable = native_root / "autodev"
            executable.write_text("native fixture\n", encoding="utf-8")
            internal_root = native_root / "_internal"
            internal_root.mkdir()

            with (
                patch.object(repo_setup.sys, "frozen", True, create=True),
                patch.object(repo_setup.sys, "executable", str(executable)),
            ):
                result = repo_setup.doctor(
                    repo,
                    github_repo=fake.repo,
                    autodev_root=internal_root,
                    runner=fake,
                    which=lambda name: f"/tools/{name}",
                )

            self.assertTrue(result.healthy)
            cli_check = next(item for item in result.checks if item.name == "cli")
            self.assertEqual(cli_check.state, "ok")
            self.assertEqual(
                cli_check.detail,
                f"native CLI executable: {executable.resolve()}",
            )
            self.assertFalse((internal_root / "automation" / "autodev_cli.py").exists())

    def test_doctor_rejects_frozen_native_cli_when_executable_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = make_repo(root)
            fake = RepoSetupGitHub()
            repo_setup.install_repo(
                repo,
                github_repo=fake.repo,
                enable_opencode=False,
                runner=fake,
            )
            missing_executable = root / "opt" / "autodev" / "autodev"
            internal_root = root / "opt" / "autodev" / "_internal"
            internal_root.mkdir(parents=True)

            with (
                patch.object(repo_setup.sys, "frozen", True, create=True),
                patch.object(repo_setup.sys, "executable", str(missing_executable)),
            ):
                result = repo_setup.doctor(
                    repo,
                    github_repo=fake.repo,
                    autodev_root=internal_root,
                    runner=fake,
                    which=lambda name: f"/tools/{name}",
                )

            self.assertFalse(result.healthy)
            cli_check = next(item for item in result.checks if item.name == "cli")
            self.assertEqual(cli_check.state, "error")
            self.assertEqual(
                cli_check.detail,
                f"native CLI executable: {missing_executable.resolve()}",
            )

    def test_doctor_fix_does_not_overwrite_malformed_user_roadmap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = make_repo(Path(temp_dir))
            fake = RepoSetupGitHub()
            repo_setup.install_repo(
                repo,
                github_repo=fake.repo,
                enable_opencode=False,
                runner=fake,
            )
            roadmap = repo / ".autodev" / "roadmap.yaml"
            malformed = "version: 1\npriority:\n  - issue: nope\nfallback: oldest\n"
            roadmap.write_text(malformed, encoding="utf-8")
            error = io.StringIO()

            with redirect_stderr(error):
                code = repo_setup.run_cli(
                    [
                        "doctor",
                        "--repo",
                        str(repo),
                        "--github-repo",
                        fake.repo,
                        "--fix",
                    ],
                    runner=fake,
                    which=lambda name: f"/tools/{name}",
                )

            self.assertEqual(code, 2)
            self.assertIn("issue priority must be a positive integer", error.getvalue())
            self.assertEqual(roadmap.read_text(encoding="utf-8"), malformed)


if __name__ == "__main__":
    unittest.main()

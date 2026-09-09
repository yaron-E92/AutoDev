from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import scheduler_registration


class RecordingCloneRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        args = list(argv)
        self.calls.append(args)
        if "clone" in args:
            worker = Path(args[-1])
            worker.mkdir(parents=True, exist_ok=True)
            (worker / ".git").mkdir(exist_ok=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


class SchedulerWorkerCloneTests(unittest.TestCase):
    def test_fresh_worker_clone_includes_authenticated_repository_url_before_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir).resolve()
            source = home / "interactive"
            source.mkdir()
            github_repo = "owner/repo"
            worker = scheduler_registration.worker_path(github_repo, home=home)
            origin = "https://github.com/owner/repo.git"
            gh = "/usr/bin/gh"
            credential_helper = "!" + subprocess.list2cmdline(
                [gh, "auth", "git-credential"]
            )
            runner = RecordingCloneRunner()

            with patch.object(
                scheduler_registration,
                "_origin_url",
                return_value=origin,
            ), patch.object(
                scheduler_registration.user_config,
                "github_repository_from_remote",
                return_value=github_repo,
            ), patch.object(
                scheduler_registration,
                "_git",
            ), patch.object(
                scheduler_registration,
                "_validate_worker_policy",
            ), patch.object(
                scheduler_registration,
                "_default_branch",
                return_value="main",
            ):
                actual_worker, default_branch = scheduler_registration._ensure_worker(
                    source,
                    github_repo,
                    home=home,
                    runner=runner,
                    which=lambda command: gh if command == "gh" else None,
                )

            self.assertEqual(actual_worker, worker)
            self.assertEqual(default_branch, "main")
            self.assertEqual(
                runner.calls[1],
                [
                    "git",
                    "-c",
                    f"credential.https://github.com.helper={credential_helper}",
                    "clone",
                    "--origin",
                    "origin",
                    origin,
                    str(worker),
                ],
            )


if __name__ == "__main__":
    unittest.main()

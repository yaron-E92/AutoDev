from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import eval_harness
from automation import eval_worktree
from automation.eval_harness_core import EvalError


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


class EvalWorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "target"
        self.repo.mkdir()
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "eval@example.invalid")
        _git(self.repo, "config", "user.name", "AutoDev Eval")
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8")
        _git(self.repo, "add", "tracked.txt")
        _git(self.repo, "commit", "-m", "base")
        self.base = _git(self.repo, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_each_isolated_worktree_starts_from_same_pinned_base(self):
        first_path: Path | None = None
        second_path: Path | None = None

        with eval_worktree.isolated_worktree(self.repo, self.base) as (first, resolved):
            first_path = first
            self.assertEqual(resolved, self.base)
            self.assertTrue(_git(first, "branch", "--show-current").startswith("autodev/eval-"))
            self.assertEqual((first / "tracked.txt").read_text(encoding="utf-8"), "base\n")
            (first / "tracked.txt").write_text("candidate-a\n", encoding="utf-8")

        self.assertIsNotNone(first_path)
        self.assertFalse(first_path.exists())
        self.assertEqual((self.repo / "tracked.txt").read_text(encoding="utf-8"), "base\n")

        with eval_worktree.isolated_worktree(self.repo, self.base) as (second, resolved):
            second_path = second
            self.assertEqual(resolved, self.base)
            self.assertTrue(_git(second, "branch", "--show-current").startswith("autodev/eval-"))
            self.assertEqual((second / "tracked.txt").read_text(encoding="utf-8"), "base\n")
            self.assertNotEqual(first_path, second_path)

        self.assertIsNotNone(second_path)
        self.assertFalse(second_path.exists())

    def test_runner_issue_branch_can_be_recreated_for_sequential_profiles(self):
        issue_branch = "autodev/issue-31-benchmark"

        for _ in range(2):
            with eval_worktree.isolated_worktree(self.repo, self.base) as (worktree, _):
                current = _git(worktree, "branch", "--show-current")
                self.assertTrue(current.startswith("autodev/eval-"))
                _git(worktree, "switch", "-c", issue_branch)
                self.assertEqual(_git(worktree, "branch", "--show-current"), issue_branch)

            self.assertEqual(_git(self.repo, "branch", "--list", issue_branch), "")
            self.assertEqual(_git(self.repo, "branch", "--list", "autodev/eval-*"), "")

    def test_dirty_source_checkout_does_not_contaminate_benchmark_worktree(self):
        (self.repo / "tracked.txt").write_text("dirty source\n", encoding="utf-8")

        with eval_worktree.isolated_worktree(self.repo, self.base) as (worktree, _):
            self.assertEqual((worktree / "tracked.txt").read_text(encoding="utf-8"), "base\n")

        self.assertEqual((self.repo / "tracked.txt").read_text(encoding="utf-8"), "dirty source\n")

    def test_live_case_wrapper_isolates_sequential_profiles_and_cleans_up(self):
        case = {
            "id": "live-case",
            "version": 1,
            "issue_text": "Change tracked.txt",
            "base_commit": self.base,
            "source": {
                "kind": "public",
                "live": {
                    "repo": str(self.repo),
                    "github_repo": "example/target",
                    "issue": 123,
                },
            },
            "expected": {},
        }
        profiles = [
            {
                "name": "profile-a",
                "provider_path": Path("a.json"),
                "provider_summary": {"roles": {}},
            },
            {
                "name": "profile-b",
                "provider_path": Path("b.json"),
                "provider_summary": {"roles": {}},
            },
        ]
        observed: list[tuple[str, Path, str, str, str]] = []

        def fake_run(
            isolated_case: dict[str, object],
            profile: dict[str, object],
            **_: object,
        ) -> dict[str, object]:
            source = isolated_case["source"]
            assert isinstance(source, dict)
            live = source["live"]
            assert isinstance(live, dict)
            worktree = Path(str(live["repo"]))
            before = (worktree / "tracked.txt").read_text(encoding="utf-8")
            branch = _git(worktree, "branch", "--show-current")
            observed.append(
                (
                    str(profile["name"]),
                    worktree,
                    before,
                    str(isolated_case["base_commit"]),
                    branch,
                )
            )
            (worktree / "tracked.txt").write_text(f"{profile['name']}\n", encoding="utf-8")
            return {"reproducibility": {}}

        with mock.patch.object(eval_harness, "_BASE_RUN_LIVE_CASE", side_effect=fake_run):
            results = [
                eval_harness.run_live_case(
                    case,
                    profile,
                    output_dir=Path(self.temp.name) / str(profile["name"]),
                    timeout_seconds=30,
                    sandbox_pr=False,
                )
                for profile in profiles
            ]

        self.assertEqual([item[2] for item in observed], ["base\n", "base\n"])
        self.assertNotEqual(observed[0][1], observed[1][1])
        self.assertTrue(all(item[3] == self.base for item in observed))
        self.assertTrue(all(item[4].startswith("autodev/eval-") for item in observed))
        self.assertTrue(all(not item[1].exists() for item in observed))
        self.assertEqual((self.repo / "tracked.txt").read_text(encoding="utf-8"), "base\n")
        self.assertTrue(all(result["reproducibility"]["isolated_worktree"] for result in results))
        self.assertTrue(all(result["reproducibility"]["benchmark_base_commit"] == self.base for result in results))

    def test_worktree_is_removed_when_live_run_raises(self):
        case = {
            "id": "live-case",
            "version": 1,
            "issue_text": "Change tracked.txt",
            "base_commit": self.base,
            "source": {
                "kind": "public",
                "live": {
                    "repo": str(self.repo),
                    "github_repo": "example/target",
                    "issue": 123,
                },
            },
            "expected": {},
        }
        profile = {
            "name": "profile-a",
            "provider_path": Path("a.json"),
            "provider_summary": {"roles": {}},
        }
        captured: list[Path] = []

        def fail(isolated_case: dict[str, object], _: dict[str, object], **__: object) -> dict[str, object]:
            source = isolated_case["source"]
            assert isinstance(source, dict)
            live = source["live"]
            assert isinstance(live, dict)
            captured.append(Path(str(live["repo"])))
            raise RuntimeError("synthetic failure")

        with mock.patch.object(eval_harness, "_BASE_RUN_LIVE_CASE", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                eval_harness.run_live_case(
                    case,
                    profile,
                    output_dir=Path(self.temp.name) / "out",
                    timeout_seconds=30,
                    sandbox_pr=False,
                )

        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0].exists())
        self.assertEqual(_git(self.repo, "branch", "--list", "autodev/eval-*"), "")

    def test_live_base_must_be_a_full_commit_sha(self):
        with self.assertRaisesRegex(EvalError, "full 40-character commit SHA"):
            with eval_worktree.isolated_worktree(self.repo, "HEAD"):
                self.fail("symbolic live base should not be accepted")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import workspace_scope


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


def _fallback_ignored(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    return normalized.startswith(".autodev-run/") or normalized.startswith("bin/")


class WorkspaceScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init")
        _git(self.repo, "config", "user.email", "autodev@example.invalid")
        _git(self.repo, "config", "user.name", "AutoDev Tests")
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        _git(self.repo, "add", "tracked.txt")
        _git(self.repo, "commit", "-m", "base")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_gitignore_and_info_exclude_do_not_enter_scope(self):
        (self.repo / ".gitignore").write_text("ignored-by-gitignore.txt\n", encoding="utf-8")
        (self.repo / "ignored-by-gitignore.txt").write_text("local\n", encoding="utf-8")
        info_exclude = Path(_git(self.repo, "rev-parse", "--git-path", "info/exclude"))
        if not info_exclude.is_absolute():
            info_exclude = self.repo / info_exclude
        info_exclude.parent.mkdir(parents=True, exist_ok=True)
        info_exclude.write_text(
            ".autodev/\n.serena/\nopencode.jsonc\n",
            encoding="utf-8",
        )
        (self.repo / ".autodev").mkdir()
        (self.repo / ".autodev" / "privacy.json").write_text("{}\n", encoding="utf-8")
        (self.repo / ".serena" / "memories").mkdir(parents=True)
        (self.repo / ".serena" / "memories" / "memory_maintenance.md").write_text(
            "local\n", encoding="utf-8"
        )
        (self.repo / "opencode.jsonc").write_text("{}\n", encoding="utf-8")
        (self.repo / "src new.txt").write_text("source\n", encoding="utf-8")

        paths = workspace_scope.workspace_paths(self.repo, fallback_ignored=_fallback_ignored)

        self.assertIn("tracked.txt", paths)
        self.assertIn(".gitignore", paths)
        self.assertIn("src new.txt", paths)
        self.assertNotIn("ignored-by-gitignore.txt", paths)
        self.assertNotIn(".autodev/privacy.json", paths)
        self.assertNotIn(".serena/memories/memory_maintenance.md", paths)
        self.assertNotIn("opencode.jsonc", paths)

    def test_configured_global_excludes_are_honored(self):
        global_ignore = Path(self.temp.name) / "global-ignore"
        global_ignore.write_text("*.private-local\n", encoding="utf-8")
        _git(self.repo, "config", "core.excludesFile", str(global_ignore))
        (self.repo / "secret.private-local").write_text("local\n", encoding="utf-8")
        (self.repo / "included.cs").write_text("code\n", encoding="utf-8")

        paths = workspace_scope.workspace_paths(self.repo, fallback_ignored=_fallback_ignored)

        self.assertNotIn("secret.private-local", paths)
        self.assertIn("included.cs", paths)

    def test_tracked_file_stays_in_scope_when_ignore_rule_later_matches(self):
        (self.repo / "kept.local").write_text("tracked\n", encoding="utf-8")
        _git(self.repo, "add", "kept.local")
        _git(self.repo, "commit", "-m", "track ignored-shaped file")
        (self.repo / ".gitignore").write_text("*.local\n", encoding="utf-8")

        paths = workspace_scope.workspace_paths(self.repo, fallback_ignored=_fallback_ignored)

        self.assertIn("kept.local", paths)

    def test_nonignored_untracked_unicode_and_whitespace_paths_are_preserved(self):
        names = ["space name.cs", "ümlaut-東京.cs"]
        for name in names:
            (self.repo / name).write_text("code\n", encoding="utf-8")

        paths = workspace_scope.workspace_paths(self.repo, fallback_ignored=_fallback_ignored)

        for name in names:
            self.assertIn(name, paths)

    def test_deleted_tracked_path_remains_enumerated_but_drops_from_snapshot(self):
        deleted = self.repo / "tracked.txt"
        before_paths = workspace_scope.workspace_paths(self.repo, fallback_ignored=_fallback_ignored)
        before_snapshot = workspace_scope.workspace_snapshot(
            self.repo, fallback_ignored=_fallback_ignored
        )
        deleted.unlink()

        after_paths = workspace_scope.workspace_paths(self.repo, fallback_ignored=_fallback_ignored)
        after_snapshot = workspace_scope.workspace_snapshot(
            self.repo, fallback_ignored=_fallback_ignored
        )

        self.assertIn("tracked.txt", before_paths)
        self.assertIn("tracked.txt", after_paths)
        self.assertIn("tracked.txt", before_snapshot)
        self.assertNotIn("tracked.txt", after_snapshot)

    def test_ignored_untracked_file_does_not_change_snapshot(self):
        info_exclude = Path(_git(self.repo, "rev-parse", "--git-path", "info/exclude"))
        if not info_exclude.is_absolute():
            info_exclude = self.repo / info_exclude
        info_exclude.parent.mkdir(parents=True, exist_ok=True)
        info_exclude.write_text("local-only/\n", encoding="utf-8")
        baseline = workspace_scope.workspace_snapshot(self.repo, fallback_ignored=_fallback_ignored)
        (self.repo / "local-only").mkdir()
        (self.repo / "local-only" / "settings.json").write_text("{}\n", encoding="utf-8")

        actual = workspace_scope.workspace_snapshot(self.repo, fallback_ignored=_fallback_ignored)

        self.assertEqual(actual, baseline)

    def test_non_git_fallback_keeps_existing_operational_exclusions(self):
        fallback = Path(self.temp.name) / "plain"
        fallback.mkdir()
        (fallback / "source.cs").write_text("code\n", encoding="utf-8")
        (fallback / "bin").mkdir()
        (fallback / "bin" / "generated.dll").write_text("generated\n", encoding="utf-8")
        (fallback / ".autodev-run").mkdir()
        (fallback / ".autodev-run" / "state.json").write_text("{}\n", encoding="utf-8")

        paths = workspace_scope.workspace_paths(fallback, fallback_ignored=_fallback_ignored)
        snapshot = workspace_scope.workspace_snapshot(fallback, fallback_ignored=_fallback_ignored)

        self.assertEqual(paths, ["source.cs"])
        self.assertEqual(list(snapshot), ["source.cs"])


if __name__ == "__main__":
    unittest.main()

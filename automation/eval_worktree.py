from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from automation.eval_harness_core import EvalError


_FULL_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise EvalError(f"live evaluation worktree setup failed: {detail}")
    return completed


def resolve_pinned_base(repo: Path, base_commit: str) -> str:
    repo = repo.expanduser().resolve()
    requested = base_commit.strip()
    if not _FULL_SHA.fullmatch(requested):
        raise EvalError(
            "live evaluation case base_commit must be a full 40-character commit SHA; "
            "use `git rev-parse HEAD` when preparing the case"
        )
    top = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if not top or Path(top).resolve() != repo:
        raise EvalError("live evaluation repo must point at the Git repository root")
    resolved = _git(repo, "rev-parse", "--verify", f"{requested}^{{commit}}").stdout.strip()
    if resolved.casefold() != requested.casefold():
        raise EvalError(f"live evaluation base commit did not resolve exactly: {requested}")
    return resolved


@contextmanager
def isolated_worktree(repo: Path, base_commit: str) -> Iterator[tuple[Path, str]]:
    repo = repo.expanduser().resolve()
    resolved = resolve_pinned_base(repo, base_commit)
    temp_root = Path(tempfile.mkdtemp(prefix="autodev-eval-"))
    worktree = temp_root / "worktree"
    added = False
    try:
        _git(repo, "worktree", "add", "--detach", str(worktree), resolved)
        added = True
        yield worktree, resolved
    finally:
        if added:
            _git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            _git(repo, "worktree", "prune", check=False)
        shutil.rmtree(temp_root, ignore_errors=True)

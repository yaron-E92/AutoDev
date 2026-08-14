from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
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
    temp_branch = f"autodev/eval-{uuid.uuid4().hex}"
    added = False
    current_branch = ""
    try:
        # The operational runner intentionally refuses detached HEADs. Give each
        # benchmark worktree its own throwaway AutoDev branch so the runner can
        # create its normal issue branch without weakening that safety check.
        _git(repo, "worktree", "add", "-b", temp_branch, str(worktree), resolved)
        added = True
        yield worktree, resolved
    finally:
        if added:
            if worktree.exists():
                current_branch = _git(
                    worktree,
                    "branch",
                    "--show-current",
                    check=False,
                ).stdout.strip()
            _git(repo, "worktree", "remove", "--force", str(worktree), check=False)
            _git(repo, "worktree", "prune", check=False)

            # run_real_issue normally creates autodev/issue-... from the
            # temporary branch. That branch is benchmark-only in this worktree;
            # remove it so the next profile can create the same issue branch
            # independently from the same pinned base.
            if current_branch and current_branch != temp_branch:
                _git(repo, "branch", "-D", current_branch, check=False)
            _git(repo, "branch", "-D", temp_branch, check=False)
        shutil.rmtree(temp_root, ignore_errors=True)

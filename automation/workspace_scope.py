from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Callable


class WorkspaceScopeError(RuntimeError):
    pass


def is_git_worktree(repo: Path) -> bool:
    repo = repo.expanduser().resolve()
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == b"true"


def workspace_paths(
    repo: Path,
    *,
    fallback_ignored: Callable[[str], bool],
) -> list[str]:
    """Return the canonical file universe for identity and shipment.

    Git repositories use Git's own tracked/nonignored-untracked view.  A
    filesystem fallback is intentionally retained only for non-Git fixtures.
    Missing tracked files remain in the returned Git path set so callers can
    represent deletions by comparing snapshots.
    """

    repo = repo.expanduser().resolve()
    if is_git_worktree(repo):
        return _git_workspace_paths(repo)
    return _filesystem_workspace_paths(repo, fallback_ignored=fallback_ignored)


def workspace_snapshot(
    repo: Path,
    *,
    fallback_ignored: Callable[[str], bool],
) -> dict[str, str]:
    repo = repo.expanduser().resolve()
    snapshot: dict[str, str] = {}
    for relative in workspace_paths(repo, fallback_ignored=fallback_ignored):
        path = repo / relative
        if not path.is_file():
            # Git includes cached paths even when the tracked file was deleted.
            # Omitting the digest lets baseline comparison report deletion.
            continue
        try:
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        except OSError:
            continue
    return snapshot


def path_is_in_scope(
    repo: Path,
    relative: str,
    *,
    fallback_ignored: Callable[[str], bool],
) -> bool:
    normalized = _normalize_relative(relative)
    return normalized in set(workspace_paths(repo, fallback_ignored=fallback_ignored))


def _git_workspace_paths(repo: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        raise WorkspaceScopeError(f"Git workspace enumeration failed: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise WorkspaceScopeError(
            "Git workspace enumeration failed"
            + (f": {detail}" if detail else f" with exit code {completed.returncode}")
        )

    paths = [_decode_git_path(item) for item in completed.stdout.split(b"\0") if item]
    # `git ls-files` should not duplicate paths here, but de-duplicate defensively
    # while keeping output deterministic across platforms/Git versions.
    return sorted(dict.fromkeys(_normalize_relative(path) for path in paths))


def _filesystem_workspace_paths(
    repo: Path,
    *,
    fallback_ignored: Callable[[str], bool],
) -> list[str]:
    values: list[str] = []
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(repo).as_posix()
        if fallback_ignored(relative):
            continue
        values.append(relative)
    return sorted(dict.fromkeys(values))


def _decode_git_path(value: bytes) -> str:
    # With `-z`, Git writes raw path bytes rather than quoted/escaped records.
    # Git for Windows emits UTF-8 paths; surrogateescape also preserves unusual
    # POSIX byte sequences without splitting on whitespace or newlines.
    return value.decode("utf-8", errors="surrogateescape")


def _normalize_relative(value: str) -> str:
    return value.replace("\\", "/").removeprefix("./")

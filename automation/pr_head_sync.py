from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from automation import workflow_stages


DEFAULT_PR_HEAD_SYNC_ATTEMPTS = 6
DEFAULT_PR_HEAD_SYNC_SECONDS = 1.0
_HEAD_MISMATCH = re.compile(
    r"^PR head (?P<actual>\S+) does not match the exact AutoDev commit (?P<expected>\S+)$"
)


def _is_expected_stale_head(state: dict[str, object], exc: BaseException) -> bool:
    match = _HEAD_MISMATCH.match(str(exc).strip())
    if match is None:
        return False
    actual = match.group("actual")
    expected = match.group("expected")
    created_parent = str(state.get("CreatedParentSha", "")).strip()
    created_commit = str(state.get("CreatedCommitSha", "")).strip()
    last_commit = str(state.get("LastCommitSha", "")).strip()
    return bool(
        created_parent
        and actual == created_parent
        and created_commit
        and expected == created_commit
        and last_commit == created_commit
    )


def ensure_pr_with_convergence(
    original: Callable[..., None],
    repo: Path,
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    attempts = workflow_stages.configured_attempt_limit(
        "PR_HEAD_SYNC_ATTEMPTS",
        DEFAULT_PR_HEAD_SYNC_ATTEMPTS,
    )
    attempts = max(1, attempts)
    interval = workflow_stages.configured_nonnegative_float(
        "PR_HEAD_SYNC_SECONDS",
        DEFAULT_PR_HEAD_SYNC_SECONDS,
    )

    last_error: workflow_stages.WorkflowStageError | None = None
    for attempt in range(1, attempts + 1):
        latest = workflow_stages.read_state(current)
        try:
            original(repo, current, latest, runner=runner)
            return
        except workflow_stages.WorkflowStageError as exc:
            if not _is_expected_stale_head(latest, exc):
                raise
            last_error = exc
            if attempt < attempts and interval:
                sleep(interval)

    if last_error is not None:
        raise last_error
    raise workflow_stages.WorkflowStageError("PR head convergence failed without an observed mismatch")


def install() -> None:
    current = workflow_stages.ensure_pr
    if getattr(current, "_autodev_pr_head_sync", False):
        return
    original = current

    def ensure_pr(
        repo: Path,
        current_dir: Path,
        state: dict[str, object],
        *,
        runner: Callable[..., object] = subprocess.run,
    ) -> None:
        ensure_pr_with_convergence(
            original,
            repo,
            current_dir,
            state,
            runner=runner,
        )

    ensure_pr._autodev_pr_head_sync = True  # type: ignore[attr-defined]
    workflow_stages.ensure_pr = ensure_pr

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from automation.windows_verification_contract import (
    AUTODEV_ROOT,
    DEFAULT_CALLER_WORKFLOW,
    MAX_CAPTURE_CHARS,
    WindowsVerificationError,
)
from automation.windows_verification_process import (
    _json_stdout,
    _returncode,
    _run,
    _stderr,
    _stdout,
)

def _current_autodev_ref(runner: Callable[..., object]) -> str:
    try:
        completed = _run(
            runner,
            ["git", "rev-parse", "HEAD"],
            cwd=AUTODEV_ROOT,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowsVerificationError(f"could not resolve the current AutoDev revision: {exc}") from exc
    if _returncode(completed) != 0:
        detail = (_stderr(completed) or _stdout(completed) or "git rev-parse failed")[-1200:]
        raise WindowsVerificationError(f"could not resolve the current AutoDev revision: {detail}")
    value = _stdout(completed)
    if len(value) != 40 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise WindowsVerificationError(
            f"current AutoDev revision must be a full 40-character Git SHA, got {value!r}"
        )
    return value

def validate_actions_installation(
    repo: Path,
    *,
    repo_full: str,
    config: dict[str, object],
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    workflow = str(config.get("workflow", DEFAULT_CALLER_WORKFLOW))
    if not repo_full:
        raise WindowsVerificationError("cannot validate Windows GitHub Actions because the target GitHub repository is unknown")

    try:
        permissions = _run(
            runner,
            ["gh", "api", f"repos/{repo_full}/actions/permissions"],
            cwd=repo,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowsVerificationError(f"could not query GitHub Actions permissions for {repo_full}: {exc}") from exc
    permissions_value = _json_stdout(permissions, "GitHub Actions permissions query")
    if isinstance(permissions_value, dict) and permissions_value.get("enabled") is False:
        raise WindowsVerificationError(
            f"GitHub Actions is disabled for {repo_full}; enable Actions before running Windows-required AutoDev verification"
        )

    try:
        view = _run(
            runner,
            ["gh", "workflow", "view", workflow, "--repo", repo_full, "--yaml"],
            cwd=repo,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowsVerificationError(f"could not query target Windows workflow {workflow}: {exc}") from exc
    if _returncode(view) != 0:
        detail = (_stderr(view) or _stdout(view) or "workflow not found")[-1200:]
        raise WindowsVerificationError(
            f"Windows verification requires .github/workflows/{workflow} on the default branch of {repo_full}, "
            "but GitHub cannot resolve it. Re-run the AutoDev installer, commit/merge the generated caller workflow "
            f"to the target default branch, then resume. GitHub said: {detail}"
        )
    return {
        "state": "ready",
        "transport": "github-actions",
        "workflow": workflow,
        "repo": repo_full,
    }

def _list_workflow_runs(
    repo: Path,
    repo_full: str,
    workflow: str,
    branch: str,
    runner: Callable[..., object],
) -> list[dict[str, object]]:
    completed = _run(
        runner,
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo_full,
            "--workflow",
            workflow,
            "--branch",
            branch,
            "--event",
            "workflow_dispatch",
            "--limit",
            "20",
            "--json",
            "databaseId,headSha,status,conclusion,url,createdAt",
        ],
        cwd=repo,
        timeout=30,
    )
    value = _json_stdout(completed, "GitHub Actions run listing")
    if not isinstance(value, list):
        raise WindowsVerificationError("GitHub Actions run listing returned a non-array JSON value")
    return [item for item in value if isinstance(item, dict)]

def _failed_logs(
    repo: Path,
    repo_full: str,
    run_id: int,
    runner: Callable[..., object],
) -> str:
    try:
        completed = _run(
            runner,
            ["gh", "run", "view", str(run_id), "--repo", repo_full, "--log-failed"],
            cwd=repo,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not retrieve failed GitHub Actions logs: {exc}"
    return (_stdout(completed) or _stderr(completed))[-MAX_CAPTURE_CHARS:]

from __future__ import annotations

import sys
from pathlib import Path

from automation import workspace_scope
from automation import workflow_stages_core as _core


def _workspace_snapshot(repo: Path) -> dict[str, str]:
    try:
        return workspace_scope.workspace_snapshot(
            repo,
            fallback_ignored=_core.ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise _core.WorkflowStageError(str(exc)) from exc


def _workspace_file_paths(repo: Path) -> list[str]:
    try:
        return workspace_scope.workspace_paths(
            repo,
            fallback_ignored=_core.ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise _core.WorkflowStageError(str(exc)) from exc


def _workspace_path_in_scope(repo: Path, relative: str) -> bool:
    try:
        return workspace_scope.path_is_in_scope(
            repo,
            relative,
            fallback_ignored=_core.ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise _core.WorkflowStageError(str(exc)) from exc


_original_create_api_commit = _core.create_api_commit


def _create_api_commit(
    repo: Path,
    state: dict[str, object],
    changes: list[dict[str, str]],
    current: Path,
    *,
    runner=_core.subprocess.run,
) -> str:
    scoped = set(_workspace_file_paths(repo))
    baseline_path, _ = _core._baseline_snapshot(current, state)
    baseline = _core.read_json(baseline_path)
    baseline_paths = set(str(path) for path in baseline) if isinstance(baseline, dict) else set()
    outside = sorted(
        str(change.get("Path", ""))
        for change in changes
        if str(change.get("Path", "")) not in scoped
        and not (
            str(change.get("Status", "")) == "deleted"
            and str(change.get("Path", "")) in baseline_paths
        )
    )
    if outside:
        raise _core.WorkflowStageError(
            "API commit refused because changed paths are outside Git's tracked/nonignored workspace scope: "
            + ", ".join(outside[:20])
        )
    return _original_create_api_commit(
        repo,
        state,
        changes,
        current,
        runner=runner,
    )


# Keep the long-standing automation.workflow_stages module API while routing the
# one canonical workspace universe through Git. Functions in the implementation
# module resolve these names from their own globals, so replacing them here makes
# source identity, workspace changes, resume drift checks, and API shipment all
# consume exactly the same scope.
_core.workspace_snapshot = _workspace_snapshot
_core.workspace_file_paths = _workspace_file_paths
_core.workspace_path_in_scope = _workspace_path_in_scope
_core.create_api_commit = _create_api_commit

if __name__ == "__main__":
    raise SystemExit(_core.main())

# Imported callers should receive the implementation module itself so existing
# monkeypatching/tests continue to operate on the globals used by stage functions.
sys.modules[__name__] = _core

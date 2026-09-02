from __future__ import annotations

from pathlib import Path
from typing import Callable

from automation import (
    queue_selection,
    scheduler_runtime_worker,
    workflow_stages,
    workflow_storage,
    workflow_workspace,
)
from automation.scheduler_process import _git, _git_status, _stdout
from automation.scheduler_types import SchedulerError, SchedulerRegistration, _now


def resolve_merged_ready_run(
    registration: SchedulerRegistration,
    existing: queue_selection.ExistingRun,
    *,
    runner: Callable[..., object],
) -> None:
    worker = Path(registration.worker_repository).expanduser().resolve()
    current = worker / workflow_stages.CURRENT_DIR
    try:
        state = workflow_stages.read_state(current)
    except Exception as exc:
        raise SchedulerError(
            f"cannot read completed AutoDev run before merged-PR worker refresh: {exc}"
        ) from exc

    pr_url = str(state.get("PrUrl", "")).strip()
    if not pr_url or pr_url != existing.pr_url:
        raise SchedulerError(
            "completed AutoDev run PR identity changed before merged-PR worker refresh"
        )

    snapshot_path = current / "last-commit-workspace-snapshot.json"
    expected_hash = str(state.get("LastCommitSnapshotHash", "")).strip()
    expected_snapshot = workflow_storage.read_json(snapshot_path)
    if not expected_hash or not isinstance(expected_snapshot, dict):
        raise SchedulerError(
            "completed AutoDev run is missing its post-shipment workspace proof; "
            "refusing to normalize the dedicated worker"
        )
    if workflow_storage._file_sha256(snapshot_path) != expected_hash:
        raise SchedulerError(
            "completed AutoDev run post-shipment workspace proof changed unexpectedly; "
            "refusing to normalize the dedicated worker"
        )

    actual_snapshot = workflow_workspace.workspace_snapshot(worker)
    if actual_snapshot != expected_snapshot:
        raise SchedulerError(
            "dedicated worker no longer matches the exact AutoDev-shipped workspace; "
            "refusing to reset or delete local changes"
        )

    local_head = _stdout(_git(worker, ["rev-parse", "HEAD"], runner=runner)).strip()
    prepared_head = str(
        state.get("PreparedLocalHeadSha", "") or state.get("BaseSha", "")
    ).strip()
    if not prepared_head or local_head != prepared_head:
        raise SchedulerError(
            "dedicated worker HEAD changed after the AutoDev API shipment; "
            "refusing merged-PR normalization"
        )

    _git(worker, ["reset", "--hard", "HEAD"], runner=runner)
    verified_changes = state.get("VerifiedChanges", [])
    added_paths = (
        sorted(
            str(item.get("path", "")).strip()
            for item in verified_changes
            if isinstance(item, dict)
            and str(item.get("status", "")).strip().casefold() == "added"
            and str(item.get("path", "")).strip()
        )
        if isinstance(verified_changes, list)
        else []
    )
    if added_paths:
        _git(worker, ["clean", "-fd", "--", *added_paths], runner=runner)

    _git(worker, ["checkout", registration.default_branch], runner=runner)
    _git(
        worker,
        ["merge", "--ff-only", f"origin/{registration.default_branch}"],
        runner=runner,
    )
    scheduler_runtime_worker.provision_worker(worker, runner=runner)
    dirty = _git_status(worker, runner=runner)
    if dirty:
        raise SchedulerError(
            f"dedicated worker became dirty while resolving merged PR {existing.pr_url}: {worker}"
        )

    state["PrMergeResolved"] = True
    state["PrMergeResolvedAt"] = _now()
    workflow_stages.write_state(current, state)

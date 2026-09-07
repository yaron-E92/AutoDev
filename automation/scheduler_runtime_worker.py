from __future__ import annotations

from pathlib import Path
from typing import Callable

from automation import role_runtime
from automation.scheduler_types import SchedulerError


def provision_worker(
    worker: Path,
    *,
    runner: Callable[..., object],
) -> None:
    """Refresh the runtime selected by the scheduler registration.

    The worker-local runtime state lives under ``.git`` so it cannot contaminate
    repository diffs. Refreshing it on every worker preparation also means tracked
    runtime assets that changed after a fetch participate in the next resume
    fingerprint before a role is invoked.
    """

    try:
        existing = role_runtime.scheduler_runtime_state(worker)
        role_runtime.refresh_scheduler_worker(
            worker,
            requested=existing.name if existing is not None else "",
            source="scheduler-registration",
            runner=runner,
        )
    except role_runtime.RoleRuntimeError as exc:
        raise SchedulerError(str(exc)) from exc


def validate_worker(
    worker: Path,
    *,
    runner: Callable[..., object],
) -> None:
    try:
        role_runtime.apply_scheduler_runtime_environment(worker)
        runtime, _ = role_runtime.select_runtime(worker)
        role_runtime.validate_scheduler_worker(
            runtime,
            worker,
            runner=runner,
        )
        role_runtime.validate_scheduler_routes(
            runtime,
            worker,
            runner=runner,
        )
    except role_runtime.RoleRuntimeError as exc:
        raise SchedulerError(str(exc)) from exc

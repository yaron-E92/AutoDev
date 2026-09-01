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
    try:
        runtime, _ = role_runtime.select_runtime(worker)
        role_runtime.provision_scheduler_worker(
            runtime,
            worker,
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
        runtime, _ = role_runtime.select_runtime(worker)
        role_runtime.validate_scheduler_worker(
            runtime,
            worker,
            runner=runner,
        )
    except role_runtime.RoleRuntimeError as exc:
        raise SchedulerError(str(exc)) from exc

from __future__ import annotations

import functools
import inspect

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO
from automation import issue_queue, privacy, privacy_grants, queue_selection, scheduler, workflow_stages

from automation import scheduler_health_contract as _m0
from automation import scheduler_health_storage as _m1
from automation import scheduler_health_probes as _m2
from automation import scheduler_health_notifications as _m3
from automation import scheduler_health_lifecycle as _m4
from automation import scheduler_health_cli as _m5

from automation.scheduler_health_contract import (
    HEALTH_FILE,
    HEALTH_SCHEMA,
    HEALTH_STATES,
    HealthSnapshot,
    NOTIFICATION_BACKENDS,
    NOTIFICATION_FILE,
    NOTIFICATION_NATIVE,
    NOTIFICATION_OFF,
    NOTIFICATION_SCHEMA,
    NotificationPolicy,
    NotificationResult,
    REMINDER_STATES,
    SchedulerHealthError,
    _iso,
    _now,
    _parse_time
)

from automation.scheduler_health_storage import (
    _read_json,
    _write_json,
    health_path,
    load_notification_policy,
    notification_path,
    save_notification_policy
)

from automation.scheduler_health_probes import (
    _blocker_counts,
    _fingerprint,
    _fingerprint_source,
    _first_issue_number,
    _privacy_grant_summary,
    _privacy_probe,
    _raw_run_status,
    compute_health,
    render_health
)

from automation.scheduler_health_notifications import (
    _native_notify,
    _notification_message,
    _should_notify,
    _snapshot_from_json,
    observe_health
)

from automation.scheduler_health_lifecycle import (
    _location_parser,
    _resolve_registration,
    current_health,
    run_tick
)

from automation.scheduler_health_cli import (
    _cleanup_health_state,
    run_cli,
    run_health,
    run_notifications,
    run_status
)

_COMPAT_MODULES = (
    _m0,
    _m1,
    _m2,
    _m3,
    _m4,
    _m5,
)
_COMPAT_MISSING = object()
_COMPAT_ORIGINALS = dict(
    (
        module,
        dict(
            (name, value)
            for name, value in module.__dict__.items()
            if name in globals() and not name.startswith("__")
        ),
    )
    for module in _COMPAT_MODULES
)
_COMPAT_BASELINE: dict[str, object] = {}


def _sync_compat_overrides() -> None:
    facade = globals()
    for module, originals in _COMPAT_ORIGINALS.items():
        namespace = module.__dict__
        for name, original in originals.items():
            current = facade.get(name, _COMPAT_MISSING)
            if current is _COMPAT_MISSING:
                continue
            baseline = _COMPAT_BASELINE.get(name, _COMPAT_MISSING)
            namespace[name] = original if current is baseline else current


def _compat_entrypoint(target):
    @functools.wraps(target)
    def invoke(*args, **kwargs):
        _sync_compat_overrides()
        return target(*args, **kwargs)
    return invoke


def _install_compat_entrypoints() -> None:
    facade = globals()
    wrapped: set[str] = set()
    for module in _COMPAT_MODULES:
        for name in tuple(module.__dict__):
            if name in wrapped or name.startswith("__") or name not in facade:
                continue
            value = facade[name]
            if inspect.isfunction(value) and value.__module__.startswith("automation."):
                facade[name] = _compat_entrypoint(value)
                wrapped.add(name)


_install_compat_entrypoints()
_COMPAT_BASELINE.update(globals())


if __name__ == "__main__":
    raise SystemExit(run_cli())

from __future__ import annotations

import functools
import inspect

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TextIO
from automation import issue_queue

from automation import claim_contract as _m0
from automation import claim_identity as _m1
from automation import claim_process as _m2
from automation import claim_repository as _m3
from automation import claim_recovery as _m4
from automation import claim_lease as _m5
from automation import claim_cli as _m6

from automation.claim_contract import (
    CLAIM_MESSAGE,
    CLAIM_REF_PREFIX,
    CLAIM_SCHEMA,
    Claim,
    ClaimAttempt,
    ClaimError,
    ClaimPolicy,
    DEFAULT_LEASE_MINUTES,
    DEFAULT_MAX_CONCURRENT_ISSUES,
    MAX_CONCURRENT_ISSUES,
    MAX_LEASE_MINUTES,
    MIN_LEASE_MINUTES,
    RecoveryResult,
    WORKER_ID_ENV,
    WORKER_SCHEMA,
    WORKER_STATE,
    WorkerIdentity,
    _WORKER_ID,
    _ZERO_SHA,
    _iso,
    _now,
    _parse_time,
    claim_ref
)

from automation.claim_identity import (
    _validate_worker_id,
    load_claim_policy,
    set_worker_identity,
    worker_identity,
    worker_state_path
)

from automation.claim_process import (
    _git,
    _is_push_race,
    _require_ok,
    _returncode,
    _run,
    _stderr,
    _stdout
)

from automation.claim_repository import (
    _base_commit,
    _claim_message,
    _claim_metadata,
    _create_claim_commit,
    _delete_with_lease,
    _new_claim,
    _parse_claim_message,
    _push_with_lease,
    _read_claim_from_ref,
    _remote_ref_sha,
    claim_expired,
    get_claim,
    list_claims
)

from automation.claim_recovery import (
    _set_running_label,
    reconcile_stale_claims,
    recovery_evidence
)

from automation.claim_lease import (
    HeartbeatLease,
    acquire_claim,
    active_claims,
    release_claim,
    renew_claim
)

from automation.claim_cli import (
    run_worker_cli
)

_COMPAT_MODULES = (
    _m0,
    _m1,
    _m2,
    _m3,
    _m4,
    _m5,
    _m6,
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

from __future__ import annotations

import functools
import inspect

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Callable
from automation import run_manifest

from automation import repair_budget_contract as _m0
from automation import repair_budget_metrics as _m1
from automation import repair_budget_policy as _m2
from automation import repair_budget_failure as _m3
from automation import repair_budget_storage as _m4
from automation import repair_budget_manifest as _m5
from automation import repair_budget_resume as _m6

from automation.repair_budget_contract import (
    ADAPTIVE_BASE_ENV,
    ADAPTIVE_MAX_ENV,
    ADAPTIVE_MIN_ENV,
    DEFAULT_ADAPTIVE_BASE,
    DEFAULT_ADAPTIVE_MAX,
    DEFAULT_ADAPTIVE_MIN,
    DEFAULT_LINES_PER_ATTEMPT,
    FAILURE_REPAIR_BUDGET_EXHAUSTED,
    FIXED_LIMIT_ENV,
    FORMULA_VERSION,
    LINES_PER_ATTEMPT_ENV,
    POLICY_ENV,
    ROOT_FAILURE_CLASSIFICATION,
    SemanticRepairBudgetError,
    _BINARY_SUFFIXES,
    _GENERATED_PREFIXES
)

from automation.repair_budget_metrics import (
    _changed_lines,
    _generated,
    _line_count,
    _path_weight,
    change_metrics
)

from automation.repair_budget_policy import (
    _nonnegative_int,
    _policy,
    _positive_int,
    _resume_budget,
    resolve_budget,
    validate_config
)

from automation.repair_budget_failure import (
    concise_failure_reason,
    failure_details,
    human_failure_summary
)

from automation.repair_budget_storage import (
    _read_json,
    _write_json,
    clear_failure_state,
    persist_budget,
    persist_failure
)

from automation.repair_budget_manifest import (
    install_run_manifest_hooks
)

from automation.repair_budget_resume import (
    _append_resume_metadata,
    _status_metadata,
    install_opencode_resume_hooks,
    maybe_reopen_exhausted_budget
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
    (module, dict(
        (name, value)
        for name, value in module.__dict__.items()
        if name in globals() and not name.startswith("__")
    ))
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

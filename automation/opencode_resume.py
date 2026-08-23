from __future__ import annotations

import functools
import inspect

import json
import subprocess
from pathlib import Path
from typing import Callable
from automation import run_manifest, workflow_stages

from automation import opencode_resume_contract as _m0
from automation import opencode_resume_manifest as _m1
from automation import opencode_resume_checkpoint as _m2
from automation import opencode_resume_status as _m3
from automation import opencode_resume_execution as _m4

from automation.opencode_resume_contract import (
    MODEL_STAGE_ROLE,
    NEXT_ACTION,
    OpenCodeResumeError,
    REPAIR_STAGE_KIND,
    ROLE_NAMES,
    has_manifest,
    manifest_path
)

from automation.opencode_resume_manifest import (
    create_open_code_manifest,
    reconcile_models,
    role_snapshots
)

from automation.opencode_resume_checkpoint import (
    _checkpoint_patch_applied,
    _existing,
    _record_incomplete_stage,
    _repair_kind,
    _source_details,
    _stage_attempt,
    _stage_for_repair_kind,
    _stage_output_hash,
    _stage_record,
    begin_role,
    checkpoint_failure,
    checkpoint_role,
    checkpoint_stage
)

from automation.opencode_resume_status import (
    _changed_role_consequences,
    _resume_problems,
    _role_for_action,
    repair_attempts,
    resume_action,
    status_text
)

from automation.opencode_resume_execution import (
    _repair_atomic_implementation_checkpoint,
    resume
)

_COMPAT_MODULES = (
    _m0,
    _m1,
    _m2,
    _m3,
    _m4,
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

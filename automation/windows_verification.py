from __future__ import annotations

import functools
import inspect

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from automation import windows_verification_contract as _m0
from automation import windows_verification_storage as _m1
from automation import windows_verification_process as _m2
from automation import windows_verification_actions as _m3
from automation import windows_verification_config as _m4
from automation import windows_verification_manifest as _m5
from automation import windows_verification_obligations as _m6
from automation import windows_verification_failure as _m7
from automation import windows_verification_execution as _m8
from automation import windows_verification_hooks as _m9

from automation.windows_verification_contract import (
    AUTODEV_ROOT,
    CONFIG_PATH,
    DEFAULT_CALLER_WORKFLOW,
    DEFAULT_POLL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    FAILURE_CODE_REPAIRABLE,
    FAILURE_DETERMINISTIC,
    FAILURE_TRANSIENT,
    MANIFEST_STAGE,
    MAX_CAPTURE_CHARS,
    REPAIR_FILE,
    REQUEST_FILE,
    RESULT_FILE,
    SCHEMA_VERSION,
    WindowsVerificationError,
    _ACTIONS_NAME_PATTERN,
    _COMMAND_MARKER,
    _TRANSIENT_MARKERS,
    utc_now
)

from automation.windows_verification_storage import (
    _read_json,
    _sha256_bytes,
    _sha256_file,
    _write_json,
    _write_text
)

from automation.windows_verification_process import (
    _json_stdout,
    _returncode,
    _run,
    _stderr,
    _stdout
)

from automation.windows_verification_actions import (
    _current_autodev_ref,
    _failed_logs,
    _list_workflow_runs,
    validate_actions_installation
)

from automation.windows_verification_config import (
    load_config,
    parse_deferred_obligations,
    safe_config_metadata,
    validate_config
)

from automation.windows_verification_manifest import (
    _verification_head,
    current_repair_attempt,
    install_manifest_hooks,
    payload_metadata,
    proof_current,
    sync_manifest,
    windows_required
)

from automation.windows_verification_obligations import (
    record_local_deferred_obligations
)

from automation.windows_verification_failure import (
    _blocked_failure,
    _infrastructure_failure,
    _looks_transient_text,
    _render_repair
)

from automation.windows_verification_execution import (
    run_after_ci,
    run_after_push,
    validate_ready
)

from automation.windows_verification_hooks import (
    install_opencode_hooks
)

_COMPAT_MODULES = (
    _m0,
    _m1,
    _m2,
    _m3,
    _m4,
    _m5,
    _m6,
    _m7,
    _m8,
    _m9,
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

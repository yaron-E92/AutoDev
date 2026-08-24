from __future__ import annotations

import functools
import inspect

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from automation import privacy

from automation import privacy_grant_contract as _m0
from automation import privacy_grant_store as _m1
from automation import privacy_grant_matching as _m2
from automation import privacy_grant_commands as _m3
from automation import privacy_grant_hooks as _m4
from automation import privacy_grant_cli as _m5

from automation.privacy_grant_contract import (
    DEFAULT_STORE,
    DURATIONS,
    DURATION_DELTAS,
    REPOSITORY_ID_ENV,
    SCOPES,
    STORE_ENV,
    STORE_VERSION,
    _BYPASS_DEPTH
)

from automation.privacy_grant_store import (
    _iso,
    _load_store,
    _normalize_github_remote,
    _now,
    _parse_time,
    _save_store,
    _store_path,
    repository_identity
)

from automation.privacy_grant_matching import (
    _grant_id,
    _grant_matches,
    _policy_fingerprint,
    _provider_identity,
    _route_identity,
    _status,
    bypass_grants,
    matching_grant
)

from automation.privacy_grant_commands import (
    create_grant,
    current_grants,
    revoke_grants
)

from automation.privacy_grant_hooks import (
    _audit_grant_use,
    _install_privacy_gate,
    _install_run_consent_hook,
    _persistent_duration_from_choice,
    _read_run_choice,
    install
)

from automation.privacy_grant_cli import (
    _parser,
    _prompt_duration,
    _resolve_requirements,
    _run_consent_cli,
    _run_revoke_cli,
    _run_status_cli,
    _select_scope_decisions,
    run_cli
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

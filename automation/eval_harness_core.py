from __future__ import annotations

import functools
import inspect

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from automation import run_manifest

from automation import evaluation_contract as _m0
from automation import evaluation_profiles as _m1
from automation import evaluation_scoring as _m2
from automation import evaluation_execution as _m3
from automation import evaluation_reporting as _m4
from automation import evaluation_cli as _m5

from automation.evaluation_contract import (
    DEFAULT_CASES,
    DEFAULT_PROFILES,
    DEFAULT_RESULTS_ROOT,
    DEPENDENCY_NAMES,
    EvalError,
    REPO_ROOT,
    SCHEMA_VERSION,
    UNKNOWN,
    utc_now
)

from automation.evaluation_profiles import (
    ensure_free_route_safety,
    fingerprint,
    load_cases,
    load_profiles,
    read_json,
    redact,
    safe_fallbacks,
    safe_headroom,
    safe_provider_summary,
    sanitized_url,
    selected_cases
)

from automation.evaluation_scoring import (
    estimate_model_calls,
    invocation_metrics,
    parse_diff,
    path_matches,
    repair_count,
    score_record,
    semantic_metrics,
    stage_record,
    stage_timing,
    unavailable_result
)

from automation.evaluation_execution import (
    git_diff,
    git_rev_parse,
    live_plan,
    load_replay,
    read_optional_json,
    run_live_case
)

from automation.evaluation_reporting import (
    aggregate,
    render_markdown,
    write_results
)

from automation.evaluation_cli import (
    build_parser,
    main,
    print_live_plan,
    validate_budgets
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
    raise SystemExit(main())

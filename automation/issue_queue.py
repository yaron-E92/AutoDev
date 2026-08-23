from __future__ import annotations

import functools
import inspect

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, TextIO

from automation import queue_contract as _m0
from automation import queue_policy as _m1
from automation import queue_github as _m2
from automation import queue_classification as _m3
from automation import queue_workflow as _m4
from automation import queue_presentation as _m5
from automation import queue_cli as _m6

from automation.queue_contract import (
    API_VERSION,
    ATTENTION_LABEL,
    BLOCKED_LABEL,
    Blocker,
    CommandResult,
    DEFAULT_LIMIT,
    LABEL_SPECS,
    MANAGED_LABEL,
    QUEUE_CONFIG,
    QueueError,
    QueueIssue,
    QueuePolicy,
    QueueState,
    READY_LABEL,
    RUNNING_LABEL,
    _label_names,
    _milestone_title
)

from automation.queue_policy import (
    load_policy
)

from automation.queue_github import (
    _json_result,
    _queue_issue,
    _run_gh,
    ensure_queue_labels,
    fetch_issue,
    list_blockers,
    list_issues,
    remove_dependency,
    resolve_github_repo
)

from automation.queue_classification import (
    _desired_derived_labels,
    _split_blockers,
    _update_derived_labels,
    classify_issue
)

from automation.queue_workflow import (
    inspect_queue,
    reconcile_queue
)

from automation.queue_presentation import (
    _state_json,
    explain_state,
    queue_summary
)

from automation.queue_cli import (
    _parser,
    run_cli
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


if __name__ == "__main__":
    raise SystemExit(run_cli())

from __future__ import annotations

import functools
import inspect

import json
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request
from automation.headroom import (
    HeadroomConfig,
    HeadroomError,
    headroom_config_from_values,
    prepare_prompt,
    proxy_headers,
)

from automation import provider_contract as _m0
from automation import provider_requests as _m1
from automation import provider_command as _m2
from automation import provider_http as _m3
from automation import provider_headroom as _m4
from automation import provider_mock as _m5
from automation import provider_factory as _m6

from automation.provider_contract import (
    ModelConfig,
    ModelProvider,
    PROVIDER_ALIASES,
    ProviderError,
    ProviderResponse,
    SAFE_HEADER_NAMES,
    SENSITIVE_HEADER_NAMES,
    SUPPORTED_PROVIDERS
)

from automation.provider_requests import (
    apply_free_only_routing,
    apply_model_selection,
    build_chat_completions_body,
    build_responses_body,
    classify_http_status,
    http_failure_message,
    response_telemetry,
    validate_output_limit,
    validate_safe_headers,
    validated_request_options
)

from automation.provider_command import (
    CommandProvider,
    quote_shell_argument
)

from automation.provider_http import (
    ChatCompletionsProvider,
    ResponsesProvider,
    _OpenAICompatibleProvider
)

from automation.provider_headroom import (
    HeadroomProvider,
    headroom_role_from_prompt,
    with_headroom_role
)

from automation.provider_mock import (
    MockProvider
)

from automation.provider_factory import (
    create_provider,
    load_provider_config,
    model_config_from_values,
    normalize_provider_name,
    object_map,
    object_string_map,
    ollama_command_for_model,
    resolve_model_config
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


_ORIGINAL_HEADROOM_INVOKE = HeadroomProvider.invoke

def _compat_headroom_invoke(self, *args, **kwargs):
    _sync_compat_overrides()
    return _ORIGINAL_HEADROOM_INVOKE(self, *args, **kwargs)

HeadroomProvider.invoke = _compat_headroom_invoke

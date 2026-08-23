#!/usr/bin/env python3
"""Compatibility facade for the responsibility-based area-reader pipeline."""

from __future__ import annotations

import functools
import inspect
import sys
from pathlib import Path

REPO_TOOL_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_TOOL_ROOT))

import argparse
import fnmatch
import json
import os
from pathlib import Path
import shlex
import sys
import time
from urllib import error, request
import xml.etree.ElementTree as ET
from area_reader_v2.command_group_recommendations import recommend_command_groups as recommend_area_reader_command_groups
from automation.model_providers import ModelConfig, create_provider, ollama_command_for_model

from area_reader_v2 import area_reader_settings as _m0
from area_reader_v2 import area_reader_cli as _m1
from area_reader_v2 import area_reader_storage as _m2
from area_reader_v2 import area_reader_repository as _m3
from area_reader_v2 import area_reader_verification as _m4
from area_reader_v2 import area_reader_routing as _m5
from area_reader_v2 import area_reader_context as _m6
from area_reader_v2 import area_reader_prompts as _m7
from area_reader_v2 import area_reader_provider as _m8
from area_reader_v2 import area_reader_execution as _m9
from area_reader_v2 import area_reader_workflow as _m10

from area_reader_v2.area_reader_settings import (
    AREA_HINTS,
    DEFAULT_AUTO_AREAS,
    DEFAULT_CODER_NUM_PREDICT,
    DEFAULT_MAX_CHARS_PER_AREA,
    DEFAULT_READER_NUM_PREDICT,
    DEFAULT_SYNTH_NUM_PREDICT,
    EXCLUDED_DIRS,
    INCLUDED_FILENAMES,
    INCLUDED_SUFFIXES,
    MARKDOWN_SMOKE_SCRIPT,
    MAX_FILE_BYTES,
    OLLAMA_CHAT_URL,
    PREFERRED_SOLUTION_FILTER_MARKERS,
    PRIORITY_PATTERNS,
    REPO_TOOL_ROOT,
    SUPPORTED_AREAS,
)

from area_reader_v2.area_reader_cli import (
    expand_user_path,
    parse_args,
)

from area_reader_v2.area_reader_storage import (
    write_executable_text,
    write_json,
    write_text,
)

from area_reader_v2.area_reader_repository import (
    area_for_file,
    build_repo_map,
    collect_repo_files,
    detect_repo_facts,
    is_included_file,
    is_priority_file,
    iter_candidate_files,
    matches_any,
    package_manager_for_root,
    package_root,
    read_csproj_facts,
    read_json_object,
    xml_local_name,
)

from area_reader_v2.area_reader_verification import (
    apply_recommended_command_groups,
    build_verification_command_groups,
    command,
    command_group,
    detect_android_sdk_available,
    dotnet_solution_targets,
    preferred_solution_filter,
    recommended_command_groups,
    render_verification_script,
    script_command_for_package,
    shell_function_name,
)

from area_reader_v2.area_reader_routing import (
    area_file_map,
    format_area_file_map,
    route_areas,
)

from area_reader_v2.area_reader_context import (
    build_area_bundle,
    read_file_for_bundle,
)

from area_reader_v2.area_reader_prompts import (
    build_area_reader_prompt,
    build_coder_prompt,
    build_synthesis_prompt,
)

from area_reader_v2.area_reader_provider import (
    build_metrics,
    call_ollama,
    call_provider,
    duration_seconds,
    extract_message,
    model_config_from_args,
    tokens_per_sec,
)

from area_reader_v2.area_reader_execution import (
    run_area_reader,
)

from area_reader_v2.area_reader_workflow import (
    main,
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
    _m10,
)
_COMPAT_MISSING = object()
_COMPAT_ORIGINALS = {
    module: {
        name: value
        for name, value in module.__dict__.items()
        if name in globals() and not name.startswith("__")
    }
    for module in _COMPAT_MODULES
}
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
            if inspect.isfunction(value) and value.__module__.startswith("area_reader_v2."):
                facade[name] = _compat_entrypoint(value)
                wrapped.add(name)


_install_compat_entrypoints()
_COMPAT_BASELINE.update(globals())


if __name__ == "__main__":
    raise SystemExit(main())

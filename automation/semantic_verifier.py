from __future__ import annotations

import functools
import inspect

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import ModelConfig, ModelProvider, ProviderError, load_provider_config
from automation.model_roles import (
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    resolve_role_configs,
)
from automation.prompt_policies import compose_prompt, role_policy_metadata

from automation import semantic_contract as _m0
from automation import semantic_configuration as _m1
from automation import semantic_schema as _m2
from automation import semantic_text as _m3
from automation import semantic_prompts as _m4
from automation import semantic_evidence as _m5
from automation import semantic_storage as _m6
from automation import semantic_artifacts as _m7
from automation import semantic_invocation as _m8
from automation import semantic_cli as _m9

from automation.semantic_contract import (
    ALLOWED_FINDING_SEVERITIES,
    ALLOWED_REQUIREMENT_STATUSES,
    ALLOWED_VERDICTS,
    ChangedFileList,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_MAX_SCHEMA_RETRIES,
    MAX_DIFF_CHARS,
    MAX_EVIDENCE_CHARS,
    MAX_REGRESSION_EVIDENCE_CHARS,
    MAX_REGRESSION_FILE_BYTES,
    MAX_REGRESSION_REFERENCES,
    MAX_REGRESSION_SYMBOLS,
    MAX_REPAIR_ATTEMPTS,
    MAX_SCHEMA_RETRIES,
    SEMANTIC_IGNORED_PARTS,
    SEMANTIC_SOURCE_SUFFIXES,
    SemanticSettings,
    SemanticVerifierError,
    _DECLARATION_PATTERNS,
    _LEGACY_ONLY_PLACEHOLDERS,
    _TEMPLATE_PLACEHOLDER
)

from automation.semantic_configuration import (
    _bounded_count,
    _config_error,
    resolve_semantic_settings,
    safe_semantic_metadata
)

from automation.semantic_schema import (
    _malformed,
    _parse_findings,
    _parse_requirements,
    _semantic_schema_errors,
    parse_semantic_output,
    semantic_result_template
)

from automation.semantic_text import (
    _bounded,
    render_template
)

from automation.semantic_prompts import (
    build_schema_repair_prompt,
    build_semantic_prompt,
    build_semantic_repair_prompt,
    default_repair_template,
    default_semantic_template,
    extract_acceptance_criteria
)

from automation.semantic_evidence import (
    _git_lines,
    _git_text,
    _is_tracked,
    _removed_symbol_candidates,
    collect_changed_files,
    collect_cross_file_regression_evidence,
    collect_current_diff,
    collect_deterministic_evidence
)

from automation.semantic_storage import (
    _read_json,
    _read_text
)

from automation.semantic_artifacts import (
    _write_result_pair,
    render_semantic_summary,
    semantic_artifact_path,
    write_final_verdict,
    write_semantic_result
)

from automation.semantic_invocation import (
    invoke_semantic_verifier,
    prepare_semantic_prompt,
    prepare_semantic_repair_prompt,
    resolve_profile_roles
)

from automation.semantic_cli import (
    build_parser,
    run
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
    raise SystemExit(run())

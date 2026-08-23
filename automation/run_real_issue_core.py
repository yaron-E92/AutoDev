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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO
from area_reader_v2 import runner as area_reader_runner
from area_reader_v2.command_group_recommendations import documentation_only_command_groups, is_documentation_only_scope
from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import (
    ModelConfig,
    ModelProvider,
    ProviderError,
    create_provider,
    load_provider_config,
    ollama_command_for_model,
    resolve_model_config,
)

from automation import issue_runner_contract as _m0
from automation import issue_runner_config as _m1
from automation import issue_runner_commands as _m2
from automation import issue_runner_repository as _m3
from automation import issue_runner_reader as _m4
from automation import issue_runner_artifacts as _m5
from automation import issue_runner_implementation as _m6
from automation import issue_runner_verification as _m7
from automation import issue_runner_prompts as _m8
from automation import issue_runner_pull_request as _m9
from automation import issue_runner_storage as _m10
from automation import issue_runner_legacy as _m11

from automation.issue_runner_contract import (
    CommandResult,
    DEFAULT_BLOCKED_LABEL,
    DEFAULT_CODER_MODEL,
    DEFAULT_DONE_LABEL,
    DEFAULT_FAILED_LABEL,
    DEFAULT_READER_MODEL,
    DEFAULT_READY_LABEL,
    DEFAULT_RUNNING_LABEL,
    FALLBACK_SYNTHESIZED_HANDOFF,
    IssueSelection,
    NO_CHANGES_REQUIRED,
    PATCH_END,
    PATCH_START,
    PROMPT_TEMPLATE_DIR,
    RUNNER_ROOT,
    RunnerError,
    VerificationResult,
)

from automation.issue_runner_config import (
    add_default_ollama_command,
    add_provider_args,
    default_ollama_command_config,
    expand_path,
    non_negative_int,
    parse_args,
    positive_int,
    provider_cli_values,
    resolve_provider_configs,
    validate_inputs,
)

from automation.issue_runner_commands import (
    format_command_failure,
    print_command,
    require_tools,
    run_command,
)

from automation.issue_runner_repository import (
    ensure_clean_worktree,
    ensure_issue_branch,
    fetch_issue_text,
    issue_branch_name,
    issue_text_from_json,
    select_issue,
    select_next_issue,
    update_issue_labels,
)

from automation.issue_runner_reader import (
    append_provider_command_args,
    run_area_reader,
)

from automation.issue_runner_artifacts import (
    build_run_summary,
    refine_recommendations_for_plan_scope,
    write_operational_outputs,
    write_provider_metadata,
)

from automation.issue_runner_implementation import (
    apply_patch_file,
    call_coder,
    extract_unified_diff,
    parse_no_changes_required,
    process_model_response,
    run_implementation_loop,
)

from automation.issue_runner_verification import (
    render_verification_summary,
    run_recommended_verification,
    write_verification_attempt,
    write_verification_result,
)

from automation.issue_runner_prompts import (
    add_workspace_path,
    build_area_reader_planner_prompt,
    build_fix_prompt,
    build_implementation_prompt,
    build_planner_prompt_from_area_reader,
    collect_area_reader_relevant_files,
    collect_workspace_paths,
    current_diff,
    planner_handoff_section,
    synthesized_handoff_or_fallback,
    usable_synthesized_handoff,
    workspace_snapshot_summary,
    write_implementation_prompt_file,
)

from automation.issue_runner_pull_request import (
    build_pr_body,
    changed_worktree_paths,
    create_draft_pr,
    first_issue_title,
    is_relative_to,
)

from automation.issue_runner_storage import (
    read_json,
    read_optional_text,
    trim_log,
    write_json,
    write_text,
)

from automation.issue_runner_legacy import (
    main,
    run,
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
    _m11,
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
            if inspect.isfunction(value) and value.__module__.startswith("automation."):
                facade[name] = _compat_entrypoint(value)
                wrapped.add(name)


_install_compat_entrypoints()
_COMPAT_BASELINE.update(globals())


if __name__ == "__main__":
    raise SystemExit(main())

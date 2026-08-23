from __future__ import annotations

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

DEFAULT_READER_MODEL = "qwen35-9b-32k"

DEFAULT_CODER_MODEL = "devstral-small2-12k"

DEFAULT_READY_LABEL = "autodev:ready"

DEFAULT_RUNNING_LABEL = "autodev:running"

DEFAULT_FAILED_LABEL = "autodev:failed"

DEFAULT_DONE_LABEL = "autodev:done"

DEFAULT_BLOCKED_LABEL = "autodev:blocked"

RUNNER_ROOT = Path(__file__).resolve().parents[1]

PROMPT_TEMPLATE_DIR = RUNNER_ROOT / "promptTemplates"

FALLBACK_SYNTHESIZED_HANDOFF = (
    "Area-reader synthesis unavailable: synthesis output was empty, too short, "
    "or contained model reasoning. Use routed areas, detected facts, relevant files, "
    "recommended commands, and the coder plan below as the planning scope."
)

PATCH_START = "BEGIN_UNIFIED_DIFF"

PATCH_END = "END_UNIFIED_DIFF"

NO_CHANGES_REQUIRED = "NO_CHANGES_REQUIRED"

@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    cwd: Path
    returncode: int
    stdout: str
    stderr: str

@dataclass(frozen=True)
class IssueSelection:
    number: int
    title: str
    url: str
    labels: list[str]
    body: str = ""

@dataclass(frozen=True)
class VerificationResult:
    attempt: int
    returncode: int
    command_group: str
    stdout: str
    stderr: str
    summary_path: Path

    @property
    def passed(self) -> bool:
        return self.returncode == 0

class RunnerError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code

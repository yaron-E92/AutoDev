from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template

AUTODEV_ROOT = Path(__file__).resolve().parents[1]

CURRENT_DIR = Path(".autodev-run") / "current"

DIAGNOSTICS_FILE = "run-diagnostics.json"

VERIFICATION_PROOF_VERSION = 1

DEFAULT_CI_CHECK_POLL_ATTEMPTS = 12

DEFAULT_CI_CHECK_POLL_SECONDS = 5.0

STAGES = (
    "preflight",
    "prepare",
    "render-implementer",
    "local-check",
    "semantic",
    "pr-and-ci",
    "ready",
    "blocked",
    "failed",
    "status",
)

DEFAULT_MAX_REPAIR_ATTEMPTS = 3

DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS = 1

FAILURE_CODE_REPAIRABLE = "code-repairable"

FAILURE_TRANSIENT = "transient/retryable-infrastructure"

FAILURE_DETERMINISTIC = "non-retryable-deterministic"

FAILURE_SETUP = "setup/configuration"

IGNORED_PREFIXES = (
    ".git/",
    ".autodev-run/",
    ".opencode/",
    "bin/",
    "obj/",
    "node_modules/",
    "dist/",
    "build/",
    "coverage/",
    ".vs/",
    ".idea/",
    ".vscode/",
    ".venv/",
    "venv/",
    "__pycache__/",
)

class WorkflowStageError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        classification: str = FAILURE_DETERMINISTIC,
    ) -> None:
        super().__init__(message)
        self.classification = classification

def issue_number_from_arguments(arguments: str) -> int:
    match = re.search(r"(?<!\d)#?(\d+)(?!\d)", arguments or "")
    return int(match.group(1)) if match else 0

def configured_attempt_limit(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise WorkflowStageError(f"{name} must be an integer") from exc
    if value < 0:
        raise WorkflowStageError(f"{name} must be zero or greater")
    return value

def configured_nonnegative_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise WorkflowStageError(f"{name} must be a number") from exc
    if value < 0:
        raise WorkflowStageError(f"{name} must be zero or greater")
    return value

def safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:120] or "issue"

def _exception_classification(error: BaseException) -> str:
    classification = str(getattr(error, "classification", "") or "")
    if classification in {
        FAILURE_CODE_REPAIRABLE,
        FAILURE_TRANSIENT,
        FAILURE_DETERMINISTIC,
        FAILURE_SETUP,
    }:
        return classification
    if classification in {"rate_limited", "timeout", "network_error", "provider_unavailable"}:
        return FAILURE_TRANSIENT
    return FAILURE_DETERMINISTIC

def concise(value: str, limit: int = 1000) -> str:
    return " ".join(str(value).split())[:limit]
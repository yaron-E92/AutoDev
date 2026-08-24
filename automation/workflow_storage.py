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
from automation.workflow_contract import (
    WorkflowStageError,
    concise,
)

def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""

def _json_evidence(value: object) -> str:
    try:
        return concise(json.dumps(value, ensure_ascii=False, sort_keys=True), 900)
    except (TypeError, ValueError):
        return concise(str(value), 900)

def read_state(current: Path) -> dict[str, object]:
    state = read_json(current / "state.json")
    if not isinstance(state, dict) or not state:
        raise WorkflowStageError(".autodev-run/current/state.json is missing or invalid")
    return state

def write_state(current: Path, state: dict[str, object]) -> None:
    write_json(current / "state.json", state)

def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""

def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")

def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")

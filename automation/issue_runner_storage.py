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
from area_reader import workflow as area_reader_runner
from area_reader.recommendations import documentation_only_command_groups, is_documentation_only_scope
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

def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

def read_optional_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""

def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")

def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")

def trim_log(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value.rstrip() + "\n"
    return value[-limit:].rstrip() + "\n"

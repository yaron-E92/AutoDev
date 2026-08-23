from __future__ import annotations

import hashlib
import json
from pathlib import Path
from automation import workflow_stages

from automation.opencode_adapter_contract import (
    OpenCodeAdapterError,
)

def _read_diagnostics(current: Path) -> dict[str, object]:
    value = _read_json(current / workflow_stages.DIAGNOSTICS_FILE)
    return value if isinstance(value, dict) else {}

def _write_diagnostics(current: Path, value: dict[str, object]) -> None:
    _write_json(current / workflow_stages.DIAGNOSTICS_FILE, value)

def _file_sha256(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""

def _read_state(current: Path) -> dict[str, object]:
    state = _read_json(current / "state.json")
    if not isinstance(state, dict) or not state:
        raise OpenCodeAdapterError(".autodev-run/current/state.json is missing or invalid")
    return state

def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""

def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")

def _write_json(path: Path, value: object, *, ensure_ascii: bool = True) -> None:
    _write_text(
        path,
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=ensure_ascii) + "\n",
    )

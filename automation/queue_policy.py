from __future__ import annotations

import json
from pathlib import Path

from automation.queue_contract import (
    QUEUE_CONFIG,
    QueueError,
    QueuePolicy,
)

def load_policy(repo: Path) -> QueuePolicy:
    path = repo.expanduser().resolve() / QUEUE_CONFIG
    if not path.is_file():
        return QueuePolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueError(f"invalid queue policy JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise QueueError(f"queue policy must be a JSON object: {path}")
    version = raw.get("version", 1)
    if version != 1:
        raise QueueError(f"unsupported queue policy version: {version}")
    value = raw.get("autonomous_execution", True)
    if not isinstance(value, bool):
        raise QueueError("queue policy autonomous_execution must be true or false")
    return QueuePolicy(autonomous_execution=value)

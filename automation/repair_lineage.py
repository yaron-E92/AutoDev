from __future__ import annotations

import hashlib
import json
from typing import MutableMapping


LOCAL_FAILURE_FINGERPRINT_KEY = "LocalCheckFailureFingerprint"
LOCAL_REPAIR_ATTEMPTS_KEY = "LocalRepairAttemptsByFingerprint"


def local_failure_fingerprint(command: str, output: str, returncode: int) -> str:
    command_lines = [
        line.strip()
        for line in (output or "").splitlines()
        if line.lstrip().startswith("+ (")
    ]
    payload = {
        "local_check": " ".join((command or "").split()),
        "failed_command": command_lines[-1] if command_lines else "",
        "returncode": int(returncode),
        "output": (output or "")[-12000:],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def register_local_failure(state: MutableMapping[str, object], fingerprint: str) -> int:
    fingerprint = str(fingerprint or "").strip()
    if not fingerprint:
        return 0
    attempts = state.get(LOCAL_REPAIR_ATTEMPTS_KEY, {})
    attempts = dict(attempts) if isinstance(attempts, dict) else {}
    count = int(attempts.get(fingerprint, 0) or 0)
    attempts[fingerprint] = count
    state[LOCAL_REPAIR_ATTEMPTS_KEY] = attempts
    state[LOCAL_FAILURE_FINGERPRINT_KEY] = fingerprint
    return count


def current_local_repair_attempt(state: MutableMapping[str, object]) -> int:
    fingerprint = str(state.get(LOCAL_FAILURE_FINGERPRINT_KEY, "") or "").strip()
    if not fingerprint:
        return 0
    attempts = state.get(LOCAL_REPAIR_ATTEMPTS_KEY, {})
    if not isinstance(attempts, dict):
        return 0
    return int(attempts.get(fingerprint, 0) or 0)


def consume_local_repair_attempt(state: MutableMapping[str, object]) -> int:
    fingerprint = str(state.get(LOCAL_FAILURE_FINGERPRINT_KEY, "") or "").strip()
    if not fingerprint:
        return 0
    attempts = state.get(LOCAL_REPAIR_ATTEMPTS_KEY, {})
    attempts = dict(attempts) if isinstance(attempts, dict) else {}
    count = int(attempts.get(fingerprint, 0) or 0) + 1
    attempts[fingerprint] = count
    state[LOCAL_REPAIR_ATTEMPTS_KEY] = attempts
    return count


def clear_current_local_failure(state: MutableMapping[str, object]) -> None:
    state.pop(LOCAL_FAILURE_FINGERPRINT_KEY, None)

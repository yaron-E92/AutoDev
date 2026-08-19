from __future__ import annotations

import json
import re
from pathlib import Path

from automation.model_output_sanitizer import sanitize_model_output


ROLE_ATTEMPT_DIR = "role-attempts"
LAST_FAILURE_FILE = "opencode-last-failure.json"
DIAGNOSTICS_FILE = "run-diagnostics.json"
MAX_RUNTIME_EXCERPT_CHARS = 2000
FAILURE_ROLE_PROTOCOL = "role-protocol-failure"
FAILURE_ROLE_PROTOCOL_EXHAUSTED = "role-protocol-exhausted"

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?i)\b(authorization|api[_-]?key|token|secret|password|cookie|proxy[_-]?authorization)"
        r"\b\s*[:=]\s*([^\s,;]+)"
    ),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
)


def redact(value: object) -> str:
    text = str(value or "")
    text = _SECRET_PATTERNS[0].sub(r"\1 <redacted>", text)
    text = _SECRET_PATTERNS[1].sub(r"\1=<redacted>", text)
    text = _SECRET_PATTERNS[2].sub("<redacted>", text)
    return text


def runtime_excerpt(value: object) -> str:
    text = redact(value).strip()
    if len(text) <= MAX_RUNTIME_EXCERPT_CHARS:
        return text
    return text[-MAX_RUNTIME_EXCERPT_CHARS:]


def inspect_artifact(path: Path | None, *, validation_error: str = "", accepted: bool = False) -> dict[str, object]:
    if path is None:
        return {
            "artifact_path": "",
            "artifact_exists": False,
            "artifact_bytes": 0,
            "artifact_chars": 0,
            "artifact_state": "not-applicable" if accepted or not validation_error else "artifact-acceptance-failed",
            "sanitized_chars": 0,
        }

    artifact_path = str(path)
    if not path.is_file():
        return {
            "artifact_path": artifact_path,
            "artifact_exists": False,
            "artifact_bytes": 0,
            "artifact_chars": 0,
            "artifact_state": "artifact-missing",
            "sanitized_chars": 0,
        }

    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return {
            "artifact_path": artifact_path,
            "artifact_exists": True,
            "artifact_bytes": 0,
            "artifact_chars": 0,
            "artifact_state": "artifact-acceptance-failed",
            "sanitized_chars": 0,
        }

    if not raw_bytes:
        return {
            "artifact_path": artifact_path,
            "artifact_exists": True,
            "artifact_bytes": 0,
            "artifact_chars": 0,
            "artifact_state": "artifact-zero-byte",
            "sanitized_chars": 0,
        }

    text = raw_bytes.decode("utf-8", errors="replace")
    sanitized = sanitize_model_output(text)
    if not sanitized:
        state = "artifact-empty-after-sanitization"
    elif accepted:
        state = "accepted"
    elif validation_error:
        lowered = validation_error.casefold()
        if "stale" in lowered or "hash" in lowered and "match" in lowered:
            state = "artifact-stale"
        elif "empty" in lowered:
            # The raw artifact is demonstrably non-empty here. An `empty` validator
            # result therefore means the contract/parser rejected its useful content.
            state = "artifact-structurally-invalid"
        else:
            state = "artifact-acceptance-failed"
    else:
        state = "produced"

    return {
        "artifact_path": artifact_path,
        "artifact_exists": True,
        "artifact_bytes": len(raw_bytes),
        "artifact_chars": len(text),
        "artifact_state": state,
        "sanitized_chars": len(sanitized),
    }


def record_attempt(
    repo: Path,
    *,
    role: str,
    phase: str,
    runtime: str,
    output_path: Path | None,
    returncode: int | None,
    elapsed_ms: int,
    stdout: object = "",
    stderr: object = "",
    accepted: bool = False,
    validation_error: str = "",
    failure_classification: str = "",
    failure_reason: str = "",
    termination: str = "completed",
    model: str = "",
) -> str:
    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"
    diagnostics_path = current / DIAGNOSTICS_FILE
    diagnostics = _read_json(diagnostics_path)

    logical_counts = diagnostics.get("role_invocations", {})
    logical = int(logical_counts.get(role, 0) or 0) if isinstance(logical_counts, dict) else 0
    if logical <= 0:
        logical = 1

    physical_counts = diagnostics.get("role_physical_attempts", {})
    if not isinstance(physical_counts, dict):
        physical_counts = {}
    physical = int(physical_counts.get(role, 0) or 0) + 1
    physical_counts[role] = physical
    diagnostics["role_physical_attempts"] = physical_counts

    if phase == "correction":
        correction_counts = diagnostics.get("protocol_correction_attempts", {})
        if not isinstance(correction_counts, dict):
            correction_counts = {}
        correction_counts[role] = int(correction_counts.get(role, 0) or 0) + 1
        diagnostics["protocol_correction_attempts"] = correction_counts

    artifact = inspect_artifact(output_path, validation_error=validation_error, accepted=accepted)
    attempt_kind = "protocol-correction" if phase == "correction" else "initial"
    record = {
        "version": 1,
        "logical_role_invocation": logical,
        "physical_role_attempt": physical,
        "attempt_kind": attempt_kind,
        "role": role,
        "runtime": runtime,
        "model": str(model or ""),
        "termination": termination,
        "returncode": returncode,
        "elapsed_ms": max(0, int(elapsed_ms)),
        **artifact,
        "accepted": bool(accepted),
        "validation_error": runtime_excerpt(validation_error),
        "stdout_excerpt": runtime_excerpt(stdout),
        "stderr_excerpt": runtime_excerpt(stderr),
        "failure_classification": str(failure_classification or ""),
        "failure_reason": runtime_excerpt(failure_reason),
    }

    attempt_dir = current / ROLE_ATTEMPT_DIR
    attempt_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{role}-{logical:02d}-{physical:02d}-{attempt_kind}.json"
    path = attempt_dir / filename
    _write_json_atomic(path, record)

    relative = f".autodev-run/current/{ROLE_ATTEMPT_DIR}/{filename}"
    diagnostics["last_role_attempt"] = relative
    diagnostics["last_role_attempt_state"] = str(record.get("artifact_state", ""))
    _write_json_atomic(diagnostics_path, diagnostics)

    last_failure_path = current / LAST_FAILURE_FILE
    if accepted:
        previous = _read_json(last_failure_path)
        if str(previous.get("role", "")) == role:
            last_failure_path.unlink(missing_ok=True)
    elif failure_classification or failure_reason:
        failure = {
            "version": 1,
            "role": role,
            "runtime": runtime,
            "attempt_kind": attempt_kind,
            "returncode": returncode,
            "termination": termination,
            "artifact_state": str(record.get("artifact_state", "")),
            "expected_artifact": str(record.get("artifact_path", "")),
            "diagnostic_path": relative,
            "logical_role_invocation": logical,
            "physical_role_attempt": physical,
            "protocol_correction_attempts": int(
                (diagnostics.get("protocol_correction_attempts", {}) or {}).get(role, 0)
            ) if isinstance(diagnostics.get("protocol_correction_attempts", {}), dict) else 0,
            "failure_classification": str(failure_classification or ""),
            "reason": runtime_excerpt(failure_reason or validation_error),
            "stdout_excerpt": str(record.get("stdout_excerpt", "")),
            "stderr_excerpt": str(record.get("stderr_excerpt", "")),
        }
        _write_json_atomic(last_failure_path, failure)

    return relative


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)

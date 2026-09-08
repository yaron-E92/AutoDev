from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from automation import role_output_contract, role_output_fallback
from automation.model_output_sanitizer import sanitize_model_output
from automation.opencode_adapter_contract import OpenCodeAdapterError


FALLBACK_MATERIALIZATION_CAPTURED_TEXT = "captured-cli-text"
FALLBACK_MATERIALIZATION_REJECTED = "captured-cli-text-rejected"
FALLBACK_MATERIALIZATION_INVOCATION_FAILED = "fallback-invocation-failed"

FALLBACK_RESULT_CAPTURED = "captured-runtime-output"
FALLBACK_RESULT_SELF_WRITTEN = "self-written-artifact"
FALLBACK_STATE_SUCCEEDED = "succeeded"
FALLBACK_STATE_PARSER_REJECTED = "parser-rejected"
FALLBACK_STATE_NO_RESULT = "no-result"
FALLBACK_DUPLICATE_ABSENT = "absent"
FALLBACK_DUPLICATE_EQUIVALENT = "equivalent"
FALLBACK_DUPLICATE_CONFLICT_OVERRIDDEN = "conflicting-overridden-by-captured-runtime-output"
FALLBACK_DUPLICATE_INVALID_OVERRIDDEN = "invalid-overridden-by-captured-runtime-output"


class FallbackTextUnavailable(OpenCodeAdapterError):
    pass


@dataclass(frozen=True)
class FallbackMaterializationOutcome:
    source: str
    state: str
    duplicate_state: str = ""
    captured_state: str = ""
    detail: str = ""

    def safe_metadata(self) -> dict[str, str]:
        return {
            "source": self.source,
            "state": self.state,
            "duplicate_state": self.duplicate_state,
            "captured_state": self.captured_state,
            "detail": _bounded_detail(self.detail),
        }


def extract_fallback_result(stdout: object, *, role: str) -> str:
    """Extract bounded completed user-visible text from OpenCode JSON events.

    The CLI's JSON mode is an event stream, not the model's raw response. Only
    completed non-synthetic text parts are promoted into an AutoDev role result;
    tool/reasoning/step/error/compaction events remain transport diagnostics only.
    """

    raw = (
        stdout.decode("utf-8", errors="replace")
        if isinstance(stdout, bytes)
        else str(stdout or "")
    )
    if not raw.strip():
        raise FallbackTextUnavailable(
            f"fallback-text {role} returned no OpenCode JSON events"
        )

    parts: list[str] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        try:
            event = json.loads(value)
        except json.JSONDecodeError as exc:
            raise OpenCodeAdapterError(
                f"fallback-text {role} emitted malformed OpenCode JSON at line {line_number}"
            ) from exc
        if not isinstance(event, dict):
            raise OpenCodeAdapterError(
                f"fallback-text {role} emitted a non-object OpenCode event at line {line_number}"
            )
        if event.get("type") != "text":
            continue

        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "text":
            raise OpenCodeAdapterError(
                f"fallback-text {role} emitted malformed text event at line {line_number}"
            )
        if part.get("synthetic") is True:
            continue
        metadata = part.get("metadata")
        if isinstance(metadata, dict) and metadata.get("compaction_continue") is True:
            continue
        text = part.get("text")
        if not isinstance(text, str):
            raise OpenCodeAdapterError(
                f"fallback-text {role} text event is missing text at line {line_number}"
            )
        cleaned = sanitize_model_output(text)
        if cleaned:
            parts.append(cleaned)

    result = sanitize_model_output("\n\n".join(parts))
    if not result:
        if role.casefold() == "reader":
            raise FallbackTextUnavailable(
                "fallback-text Reader produced no completed factual text event"
            )
        raise FallbackTextUnavailable(
            f"fallback-text {role} produced no completed role-result text event"
        )
    if len(result) > role_output_fallback.MAX_FALLBACK_ROLE_CHARS:
        raise OpenCodeAdapterError(
            f"fallback-text {role} result exceeds the "
            f"{role_output_fallback.MAX_FALLBACK_ROLE_CHARS}-character AutoDev role-result limit"
        )
    return result


def extract_fallback_handoff(stdout: object) -> str:
    """Backward-compatible Reader wrapper used by #288 regression coverage."""

    return extract_fallback_result(stdout, role="Reader")


def correction_after_fallback(
    repo: Path,
    *,
    role: str,
    phase: str,
    has_contract: bool,
) -> bool:
    """Keep one logical fallback invocation on fallback during correction."""

    if phase != "correction" or not has_contract:
        return False
    diagnostics = _read_diagnostics(
        repo.expanduser().resolve() / ".autodev-run" / "current" / "run-diagnostics.json"
    )
    previous = diagnostics.get("last_structured_output", {})
    return bool(
        isinstance(previous, dict)
        and str(previous.get("role", "") or "") == role
        and str(previous.get("mode", "") or "") == "fallback-text"
    )


def materialize_fallback_result(
    repo: Path,
    contract: role_output_contract.RoleOutputContract | None,
    stdout: object,
) -> FallbackMaterializationOutcome:
    """Converge fallback transport on AutoDev-owned artifact materialization.

    Captured runtime output is authoritative when it parses successfully. A valid
    legacy self-written artifact remains a compatibility fallback when captured
    output is absent/rejected. If both are valid and disagree, the captured result
    deterministically wins and the conflict is exposed in content-free diagnostics.
    """

    if contract is None or not role_output_fallback.supported(contract):
        return FallbackMaterializationOutcome(source="", state="not-supported")

    repo = repo.expanduser().resolve()
    existing = None
    existing_error = ""
    try:
        existing = role_output_fallback.parse_existing(repo, contract)
    except role_output_fallback.RoleOutputFallbackError as exc:
        existing_error = str(exc)

    captured_state = FALLBACK_STATE_SUCCEEDED
    try:
        text = extract_fallback_result(stdout, role=contract.role)
        candidate = role_output_fallback.parse_candidate(repo, contract, text)
    except FallbackTextUnavailable as exc:
        captured_state = FALLBACK_STATE_NO_RESULT
        if existing is not None:
            return FallbackMaterializationOutcome(
                source=FALLBACK_RESULT_SELF_WRITTEN,
                state=FALLBACK_STATE_SUCCEEDED,
                captured_state=captured_state,
                detail=str(exc),
            )
        return FallbackMaterializationOutcome(
            source="",
            state=FALLBACK_STATE_NO_RESULT,
            captured_state=captured_state,
            detail=existing_error or str(exc),
        )
    except (OpenCodeAdapterError, role_output_fallback.RoleOutputFallbackError) as exc:
        captured_state = FALLBACK_STATE_PARSER_REJECTED
        if existing is not None:
            return FallbackMaterializationOutcome(
                source=FALLBACK_RESULT_SELF_WRITTEN,
                state=FALLBACK_STATE_SUCCEEDED,
                captured_state=captured_state,
                detail=str(exc),
            )
        return FallbackMaterializationOutcome(
            source="",
            state=FALLBACK_STATE_PARSER_REJECTED,
            captured_state=captured_state,
            detail=existing_error or str(exc),
        )

    duplicate_state = FALLBACK_DUPLICATE_ABSENT
    if existing is not None:
        duplicate_state = (
            FALLBACK_DUPLICATE_EQUIVALENT
            if existing.canonical_text == candidate.canonical_text
            else FALLBACK_DUPLICATE_CONFLICT_OVERRIDDEN
        )
    elif existing_error:
        duplicate_state = FALLBACK_DUPLICATE_INVALID_OVERRIDDEN

    try:
        role_output_fallback.materialize_candidate(repo, contract, candidate)
    except role_output_fallback.RoleOutputFallbackError as exc:
        return FallbackMaterializationOutcome(
            source="",
            state=FALLBACK_STATE_PARSER_REJECTED,
            duplicate_state=duplicate_state,
            captured_state=FALLBACK_STATE_PARSER_REJECTED,
            detail=str(exc),
        )
    return FallbackMaterializationOutcome(
        source=FALLBACK_RESULT_CAPTURED,
        state=FALLBACK_STATE_SUCCEEDED,
        duplicate_state=duplicate_state,
        captured_state=FALLBACK_STATE_SUCCEEDED,
    )


def materialize_reader_fallback(
    repo: Path,
    contract: role_output_contract.RoleOutputContract | None,
    stdout: object,
) -> Path:
    if contract is None or contract.role != "reader":
        raise OpenCodeAdapterError(
            "Reader fallback materialization requires the AutoDev Reader output contract"
        )
    outcome = materialize_fallback_result(repo, contract, stdout)
    if outcome.state != FALLBACK_STATE_SUCCEEDED:
        raise OpenCodeAdapterError(
            "fallback-text Reader handoff could not be materialized: "
            + (outcome.detail or outcome.state)
        )
    target = (
        repo.expanduser().resolve()
        / ".autodev-run"
        / "current"
        / contract.output_artifact
    )
    if not target.is_file():
        raise OpenCodeAdapterError(
            "fallback-text Reader handoff did not produce reader-brief.md"
        )
    return target


def record_fallback_materialization(
    repo: Path,
    *,
    role: str,
    phase: str,
    outcome: FallbackMaterializationOutcome,
) -> None:
    """Persist only content-free materialization evidence for role-attempt diagnostics."""

    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    path = current / "run-diagnostics.json"
    diagnostics = _read_diagnostics(path)
    logical_counts = diagnostics.get("role_invocations", {})
    logical = (
        int(logical_counts.get(role, 0) or 0)
        if isinstance(logical_counts, dict)
        else 0
    )
    records = diagnostics.setdefault("fallback_materialization", {})
    if not isinstance(records, dict):
        records = {}
        diagnostics["fallback_materialization"] = records
    records[f"{role}:{phase}"] = {
        "logical_role_invocation": max(1, logical),
        **outcome.safe_metadata(),
    }
    _write_diagnostics(path, diagnostics)


def record_reader_fallback_materialization(repo: Path, state: str) -> None:
    """Persist legacy #288 content-free Reader recovery evidence."""

    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    path = current / "run-diagnostics.json"
    diagnostics = _read_diagnostics(path)
    diagnostics["reader_fallback_materialization"] = {
        "source": FALLBACK_MATERIALIZATION_CAPTURED_TEXT,
        "state": str(state or ""),
    }
    _write_diagnostics(path, diagnostics)


def _read_diagnostics(path: Path) -> dict[str, object]:
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def _write_diagnostics(path: Path, diagnostics: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise OpenCodeAdapterError(
            f"cannot persist fallback materialization diagnostics: {exc}"
        ) from exc


def _bounded_detail(value: object) -> str:
    return " ".join(str(value or "").split())[:500]

from __future__ import annotations

import json
from pathlib import Path

from automation import role_output_contract
from automation.model_output_sanitizer import sanitize_model_output
from automation.opencode_adapter_contract import MAX_HANDOFF_CHARS, OpenCodeAdapterError


FALLBACK_MATERIALIZATION_CAPTURED_TEXT = "captured-cli-text"
FALLBACK_MATERIALIZATION_REJECTED = "captured-cli-text-rejected"
FALLBACK_MATERIALIZATION_INVOCATION_FAILED = "fallback-invocation-failed"


def extract_fallback_handoff(stdout: object) -> str:
    """Extract completed user-visible text from `opencode run --format json`.

    OpenCode's JSON mode is an event stream, not the model's raw response. Only
    completed `text` events are eligible for Reader fallback materialization;
    tool, reasoning, step, error, and explicitly synthetic text events are never
    promoted into AutoDev's durable Reader protocol artifact.
    """

    if isinstance(stdout, bytes):
        raw = stdout.decode("utf-8", errors="replace")
    else:
        raw = str(stdout or "")
    if not raw.strip():
        raise OpenCodeAdapterError(
            "fallback-text Reader returned no OpenCode JSON events"
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
                f"fallback-text Reader emitted malformed OpenCode JSON at line {line_number}"
            ) from exc
        if not isinstance(event, dict):
            raise OpenCodeAdapterError(
                f"fallback-text Reader emitted a non-object OpenCode event at line {line_number}"
            )
        if event.get("type") != "text":
            continue

        part = event.get("part")
        if not isinstance(part, dict) or part.get("type") != "text":
            raise OpenCodeAdapterError(
                f"fallback-text Reader emitted malformed text event at line {line_number}"
            )
        if part.get("synthetic") is True:
            continue
        metadata = part.get("metadata")
        if isinstance(metadata, dict) and metadata.get("compaction_continue") is True:
            continue
        text = part.get("text")
        if not isinstance(text, str):
            raise OpenCodeAdapterError(
                f"fallback-text Reader text event is missing text at line {line_number}"
            )
        cleaned = sanitize_model_output(text)
        if cleaned:
            parts.append(cleaned)

    handoff = sanitize_model_output("\n\n".join(parts))
    if not handoff:
        raise OpenCodeAdapterError(
            "fallback-text Reader produced no completed factual text event"
        )
    if len(handoff) > MAX_HANDOFF_CHARS:
        raise OpenCodeAdapterError(
            f"fallback-text Reader result exceeds the {MAX_HANDOFF_CHARS}-character AutoDev handoff limit"
        )
    return handoff


def materialize_reader_fallback(
    repo: Path,
    contract: role_output_contract.RoleOutputContract | None,
    stdout: object,
) -> Path:
    if contract is None or contract.role != "reader":
        raise OpenCodeAdapterError(
            "Reader fallback materialization requires the AutoDev Reader output contract"
        )
    handoff = extract_fallback_handoff(stdout)
    try:
        target = role_output_contract.materialize_structured_output(
            repo,
            contract,
            {"handoff_markdown": handoff},
        )
    except role_output_contract.RoleOutputContractError as exc:
        raise OpenCodeAdapterError(
            f"fallback-text Reader handoff could not be materialized: {exc}"
        ) from exc
    if target is None:
        raise OpenCodeAdapterError(
            "fallback-text Reader handoff did not produce reader-brief.md"
        )
    return target


def record_reader_fallback_materialization(repo: Path, state: str) -> None:
    """Persist content-free recovery evidence alongside normal run diagnostics."""

    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    path = current / "run-diagnostics.json"
    diagnostics: dict[str, object] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            diagnostics = loaded
    diagnostics["reader_fallback_materialization"] = {
        "source": "captured-cli-text",
        "state": str(state or ""),
    }
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
            f"cannot persist Reader fallback materialization diagnostics: {exc}"
        ) from exc

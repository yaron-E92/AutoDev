from __future__ import annotations

import json
from pathlib import Path

from automation.windows_verification_contract import (
    FAILURE_DETERMINISTIC,
    FAILURE_TRANSIENT,
    MANIFEST_STAGE,
    REQUEST_FILE,
    RESULT_FILE,
    _TRANSIENT_MARKERS,
)
from automation.windows_verification_manifest import (
    sync_manifest,
)
from automation.windows_verification_storage import (
    _write_json,
    _write_text,
)

def _looks_transient_text(value: str) -> bool:
    lowered = str(value or "").casefold()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)

def _blocked_failure(
    repo: Path,
    current: Path,
    state: dict[str, object],
    attempt: int,
    reason: str,
) -> dict[str, object]:
    state.pop("WindowsVerificationProof", None)
    state["Status"] = "WindowsVerificationBlocked"
    state["LastWindowsVerificationFailure"] = {
        "classification": FAILURE_DETERMINISTIC,
        "attempt": attempt,
        "reason": str(reason)[:2000],
    }
    _write_json(current / "state.json", state)
    sync_manifest(repo, state)
    return {
        "state": "BLOCKED",
        "failed_stage": "windows-verification",
        "reason": str(reason)[:2000],
        "failure_classification": FAILURE_DETERMINISTIC,
        "next_action": "install/enable the target GitHub Actions Windows caller workflow, then resume",
        "artifact": str(current / "deferred-verification.json"),
        "platform_verification_stage": MANIFEST_STAGE,
        "windows_repair_attempt": attempt,
    }

def _infrastructure_failure(
    repo: Path,
    current: Path,
    state: dict[str, object],
    attempt: int,
    reason: str,
    *,
    classification: str = FAILURE_TRANSIENT,
    preserve_result: bool = False,
    run_id: int = 0,
    run_url: str = "",
) -> dict[str, object]:
    state.pop("WindowsVerificationProof", None)
    state["Status"] = "WindowsVerificationInfrastructureFailed"
    state["LastWindowsVerificationFailure"] = {
        "classification": classification,
        "attempt": attempt,
        "reason": str(reason)[:2000],
        "run_id": run_id,
        "run_url": run_url,
    }
    _write_json(current / "state.json", state)
    sync_manifest(repo, state)
    return {
        "state": "FAILED",
        "failed_stage": "windows-verification",
        "reason": str(reason)[:2000],
        "failure_classification": classification,
        "next_action": "retry or correct GitHub Actions Windows verification infrastructure, then resume",
        "artifact": str(current / RESULT_FILE) if preserve_result else str(current / REQUEST_FILE),
        "platform_verification_stage": MANIFEST_STAGE,
        "windows_repair_attempt": attempt,
    }

def _render_repair(
    current: Path,
    state: dict[str, object],
    result: dict[str, object],
    path: Path,
) -> None:
    try:
        issue = (current / "issue.md").read_text(encoding="utf-8")
    except OSError:
        issue = str(state.get("IssueText", ""))
    failures = [
        item
        for item in result.get("commands", [])
        if isinstance(item, dict) and int(item.get("returncode", 0) or 0) != 0
    ]
    evidence = json.dumps(failures, indent=2, sort_keys=True)
    _write_text(
        path,
        "# Windows verification repair\n\n"
        "Fix only the code defect demonstrated by the GitHub-hosted Windows verification lane. "
        "Do not weaken or remove Windows verification. After the repair, AutoDev will rerun deterministic, semantic, push, Windows verification, PR and CI.\n\n"
        f"## Pushed head\n{state.get('LastCommitSha', '')}\n\n"
        f"## Verified source identity\n{state.get('ShippedSourceIdentity', '')}\n\n"
        f"## GitHub Actions run\n{result.get('run_url', '')}\n\n"
        f"## Issue\n{issue.strip()}\n\n"
        f"## Failing Windows evidence\n```json\n{evidence}\n```\n",
    )

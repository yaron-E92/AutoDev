from __future__ import annotations

import json
from pathlib import Path
from automation import run_manifest

from automation.repair_budget_contract import (
    FAILURE_REPAIR_BUDGET_EXHAUSTED,
    ROOT_FAILURE_CLASSIFICATION,
)
from automation.repair_budget_failure import (
    concise_failure_reason,
)

def persist_budget(repo: Path, state: dict[str, object], budget: dict[str, object]) -> None:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state["SemanticRepairBudget"] = budget
    _write_json(current / "state.json", state)
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError:
        return
    semantic = manifest.setdefault("semantic_verification", {})
    if isinstance(semantic, dict):
        semantic["repair_budget"] = budget
    run_manifest.save_manifest(manifest_path, manifest)

def persist_failure(repo: Path, state: dict[str, object], details: dict[str, object]) -> None:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state["LastSemanticFailureDetails"] = details
    _write_json(current / "state.json", state)
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError:
        return
    manifest["failure"] = {
        "classification": FAILURE_REPAIR_BUDGET_EXHAUSTED,
        "root_classification": ROOT_FAILURE_CLASSIFICATION,
        "reason": concise_failure_reason(details),
        "stage": "semantic-verified",
        "fingerprint": str(details.get("failure_fingerprint", "")),
        "details": details,
        "recorded_at": run_manifest.utc_now(),
    }
    semantic = manifest.setdefault("semantic_verification", {})
    if isinstance(semantic, dict):
        semantic["repair_budget"] = details.get("budget", {})
    run_manifest.save_manifest(manifest_path, manifest)

def clear_failure_state(repo: Path, state: dict[str, object]) -> None:
    if "LastSemanticFailureDetails" not in state:
        return
    state.pop("LastSemanticFailureDetails", None)
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    _write_json(current / "state.json", state)

def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

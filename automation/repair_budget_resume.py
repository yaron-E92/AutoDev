from __future__ import annotations

from pathlib import Path
from automation import run_manifest

from automation.repair_budget_contract import (
    FAILURE_REPAIR_BUDGET_EXHAUSTED,
    ROOT_FAILURE_CLASSIFICATION,
)
from automation.repair_budget_policy import (
    resolve_budget,
)
from automation.repair_budget_storage import (
    _read_json,
    _write_json,
)

def maybe_reopen_exhausted_budget(repo: Path, *, fixed_default: int = 2) -> bool:
    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"
    manifest_path = current / run_manifest.MANIFEST_NAME
    state = _read_json(current / "state.json")
    if not isinstance(state, dict) or not manifest_path.is_file():
        return False
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError:
        return False
    failure = manifest.get("failure", {})
    if not isinstance(failure, dict) or failure.get("classification") != FAILURE_REPAIR_BUDGET_EXHAUSTED:
        return False
    details = failure.get("details", {})
    if not isinstance(details, dict):
        return False
    previous = state.get("SemanticRepairBudget", details.get("budget", {}))
    if not isinstance(previous, dict):
        return False
    attempt = int(details.get("attempted_repairs", previous.get("attempts_consumed", 0)) or 0)
    old_limit = int(previous.get("effective_limit", 0) or 0)
    state["SemanticRepairBudget"] = previous
    raised = resolve_budget(
        repo,
        state,
        attempt=attempt,
        fixed_default=fixed_default,
    )
    new_limit = int(raised.get("effective_limit", 0) or 0)
    if new_limit <= old_limit:
        return False

    state["SemanticRepairBudget"] = raised
    state["Status"] = "SemanticRepairRequired"
    state.pop("LastSemanticFailureDetails", None)
    _write_json(current / "state.json", state)

    semantic = manifest.setdefault("semantic_verification", {})
    if isinstance(semantic, dict):
        semantic["repair_budget"] = raised
    stages = manifest.setdefault("stages", {})
    stage = stages.get("semantic-verified", {}) if isinstance(stages, dict) else {}
    if not isinstance(stage, dict):
        stage = {}
    stage_details = stage.get("details", {})
    stage_details = dict(stage_details) if isinstance(stage_details, dict) else {}
    stage_details.update(
        {
            "attempt": attempt,
            "repair_kind": "semantic",
            "reason": "semantic repair budget increased; resume at pending semantic repair",
            "failure_classification": ROOT_FAILURE_CLASSIFICATION,
            "artifact": str(details.get("repair_artifact", "")),
            "semantic_repair_budget": raised,
        }
    )
    stage.update(
        {
            "status": "repair-required",
            "updated_at": run_manifest.utc_now(),
            "details": stage_details,
        }
    )
    if isinstance(stages, dict):
        stages["semantic-verified"] = stage
    manifest["failure"] = {}
    manifest["current_stage"] = "semantic-verified"
    run_manifest.save_manifest(manifest_path, manifest)
    return True

def install_opencode_resume_hooks() -> None:
    from automation import opencode_resume

    if getattr(opencode_resume.resume, "_autodev_semantic_budget_hook", False):
        return
    original_resume = opencode_resume.resume
    original_status = opencode_resume.status_text

    def resume(repo: Path, mappings: dict[str, dict[str, str]], **kwargs):
        maybe_reopen_exhausted_budget(Path(repo))
        payload = original_resume(repo, mappings, **kwargs)
        _append_resume_metadata(Path(repo), payload)
        return payload

    def status_text(repo: Path, mappings: dict[str, dict[str, str]], **kwargs):
        text = original_status(repo, mappings, **kwargs).rstrip()
        extra = _status_metadata(Path(repo))
        return text + ("\n" + extra if extra else "") + "\n"

    setattr(resume, "_autodev_semantic_budget_hook", True)
    setattr(status_text, "_autodev_semantic_budget_hook", True)
    opencode_resume.resume = resume
    opencode_resume.status_text = status_text

def _append_resume_metadata(repo: Path, payload: dict[str, object]) -> None:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state = _read_json(current / "state.json")
    if isinstance(state, dict) and isinstance(state.get("SemanticRepairBudget"), dict):
        payload["semantic_repair_budget"] = state["SemanticRepairBudget"]
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        failure = run_manifest.load_manifest(manifest_path).get("failure", {})
    except run_manifest.ManifestError:
        return
    if isinstance(failure, dict) and isinstance(failure.get("details"), dict):
        payload["semantic_failure"] = failure["details"]

def _status_metadata(repo: Path) -> str:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state = _read_json(current / "state.json")
    budget = state.get("SemanticRepairBudget", {}) if isinstance(state, dict) else {}
    lines: list[str] = []
    if isinstance(budget, dict) and budget:
        lines.append(
            "Semantic repair budget: "
            f"policy={budget.get('policy', '')} "
            f"consumed={budget.get('attempts_consumed', 0)} "
            f"limit={budget.get('effective_limit', 0)}"
        )
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return "\n".join(lines)
    try:
        failure = run_manifest.load_manifest(manifest_path).get("failure", {})
    except run_manifest.ManifestError:
        return "\n".join(lines)
    details = failure.get("details", {}) if isinstance(failure, dict) else {}
    if not isinstance(details, dict) or not details:
        return "\n".join(lines)
    lines.append("Semantic budget failure details:")
    lines.append(f"  attempted/max: {details.get('attempted_repairs', 0)}/{details.get('maximum_repairs', 0)}")
    lines.append(f"  repair brief: {details.get('repair_brief', '')}")
    lines.append(f"  verification result: {details.get('verification_result', '')}")
    lines.append(f"  verified source identity: {details.get('verified_source_identity', '')}")
    lines.append(f"  failure fingerprint: {details.get('failure_fingerprint', '')}")
    requirements = details.get("requirements", [])
    if isinstance(requirements, list):
        for item in requirements:
            if isinstance(item, dict):
                lines.append(f"  requirement [{item.get('status', '')}]: {item.get('criterion', '')}")
    findings = details.get("findings", [])
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict):
                path = str(item.get("path", ""))
                lines.append(f"  blocking finding{f' ({path})' if path else ''}: {item.get('message', '')}")
    return "\n".join(lines)

from __future__ import annotations

from pathlib import Path
from automation import repair_lineage, run_manifest, workflow_stages

from automation.opencode_resume_contract import (
    OpenCodeResumeError,
    REPAIR_STAGE_KIND,
    has_manifest,
    manifest_path,
)
from automation.opencode_resume_manifest import (
    reconcile_models,
)

def begin_role(repo: Path, role: str, arguments: str) -> None:
    if role != "fixer" or not has_manifest(repo):
        return
    kind = _repair_kind(arguments)
    if not kind:
        return
    path = manifest_path(repo)
    manifest = run_manifest.load_manifest(path)
    target_stage = _stage_for_repair_kind(kind)
    attempt = _stage_attempt(manifest, target_stage) + 1
    if kind == "local":
        current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
        state = workflow_stages.read_state(current)
        fingerprint = str(state.get(repair_lineage.LOCAL_FAILURE_FINGERPRINT_KEY, "") or "")
        if fingerprint:
            attempt = repair_lineage.consume_local_repair_attempt(state)
            workflow_stages.write_state(current, state)
    run_manifest.record_stage_state(
        path,
        "repair-generated",
        status="in-progress",
        details={"kind": kind, "attempt": attempt},
    )

def checkpoint_role(
    repo: Path,
    role: str,
    outputs: list[Path],
    mappings: dict[str, dict[str, str]],
) -> None:
    repo = repo.expanduser().resolve()
    path = manifest_path(repo)
    if not path.is_file():
        return
    current = repo / workflow_stages.CURRENT_DIR
    reconcile_models(repo, mappings)
    manifest = run_manifest.load_manifest(path)
    try:
        if role == "reader":
            artifacts = _existing(
                current,
                "reader-brief.md",
                "routed-areas.json",
                "detected-facts.json",
                "recommended-command-groups.json",
                "verification-command-groups.json",
            )
            run_manifest.complete_stage(
                path,
                "repository-read",
                run_root=current,
                artifacts=artifacts,
                inputs={
                    "issue_sha256": run_manifest.hash_file(current / "issue.md"),
                    "reader_fingerprint": run_manifest.stage_role_fingerprint(manifest, "reader"),
                },
                details={
                    "refreshable_artifacts": [
                        "detected-facts.json",
                        "verification-command-groups.json",
                        "recommended-command-groups.json",
                    ]
                },
            )
            return
        if role == "synthesizer":
            run_manifest.complete_stage(
                path,
                "handoff-synthesized",
                run_root=current,
                artifacts=[current / "synthesized-handoff.md"],
                inputs={
                    "repository_read_output": _stage_output_hash(manifest, "repository-read"),
                    "synthesizer_fingerprint": run_manifest.stage_role_fingerprint(manifest, "synthesizer"),
                },
            )
            return
        if role == "planner":
            run_manifest.complete_stage(
                path,
                "plan-created",
                run_root=current,
                artifacts=[current / "plan.md"],
                inputs={
                    "handoff_output": _stage_output_hash(manifest, "handoff-synthesized"),
                    "planner_fingerprint": run_manifest.stage_role_fingerprint(manifest, "planner"),
                },
            )
            return
        if role == "implementer":
            proof = workflow_stages.source_identity(repo, current, workflow_stages.read_state(current))
            run_manifest.complete_stage(
                path,
                "implementation-generated",
                run_root=current,
                artifacts=[current / "commit-message.txt"],
                inputs={
                    "plan_output": _stage_output_hash(manifest, "plan-created"),
                    "implementer_fingerprint": run_manifest.stage_role_fingerprint(manifest, "implementer"),
                },
                details=_source_details(proof),
            )
            _checkpoint_patch_applied(path, current, proof, kind="implementation", attempt=0)
            return
        if role == "fixer":
            manifest = run_manifest.load_manifest(path)
            repair = _stage_record(manifest, "repair-generated")
            details = repair.get("details", {}) if isinstance(repair, dict) else {}
            kind = str(details.get("kind", "")) if isinstance(details, dict) else ""
            attempt = int(details.get("attempt", 0) or 0) if isinstance(details, dict) else 0
            if not kind:
                raise OpenCodeResumeError("fixer completion has no durable repair kind in the run manifest")
            run_manifest.invalidate_role(path, "fixer", reason=f"OpenCode {kind} repair applied")
            proof = workflow_stages.source_identity(repo, current, workflow_stages.read_state(current))
            run_manifest.complete_stage(
                path,
                "repair-generated",
                run_root=current,
                inputs={
                    "fixer_fingerprint": run_manifest.stage_role_fingerprint(run_manifest.load_manifest(path), "fixer"),
                    "kind": kind,
                    "attempt": attempt,
                },
                details={"kind": kind, "attempt": attempt, **_source_details(proof)},
            )
            _checkpoint_patch_applied(path, current, proof, kind=kind, attempt=attempt)
            pending_details = {"attempt": attempt, "repair_kind": kind}
            if kind == "local":
                state = workflow_stages.read_state(current)
                pending_details["failure_fingerprint"] = str(
                    state.get(repair_lineage.LOCAL_FAILURE_FINGERPRINT_KEY, "") or ""
                )
            run_manifest.record_stage_state(
                path,
                _stage_for_repair_kind(kind),
                status="pending",
                details=pending_details,
            )
            return
        if role == "verifier":
            return
    except (run_manifest.ManifestError, workflow_stages.WorkflowStageError) as exc:
        raise OpenCodeResumeError(str(exc)) from exc

def checkpoint_stage(repo: Path, name: str, payload: dict[str, object], attempt: int) -> None:
    repo = repo.expanduser().resolve()
    path = manifest_path(repo)
    if not path.is_file():
        return
    current = repo / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)
    outcome = str(payload.get("state", ""))
    try:
        if name == "local-check":
            if outcome == "CONTINUE":
                run_manifest.complete_stage(
                    path,
                    "deterministic-verified",
                    run_root=current,
                    artifacts=[current / "local-check.log"],
                    inputs={"source_identity": str(state.get("VerifiedSourceIdentity", ""))},
                    details={
                        "attempt": attempt,
                        "source_identity": str(state.get("VerifiedSourceIdentity", "")),
                        "parent_sha": str(state.get("VerifiedParentSha", "")),
                    },
                )
            else:
                _record_incomplete_stage(path, "deterministic-verified", outcome, attempt, payload)
            return
        if name == "semantic":
            if outcome == "CONTINUE":
                run_manifest.complete_stage(
                    path,
                    "semantic-verified",
                    run_root=current,
                    artifacts=_existing(current, "verification-result.json", "verification/final-verdict.json"),
                    inputs={
                        "deterministic_output": _stage_output_hash(run_manifest.load_manifest(path), "deterministic-verified"),
                        "source_identity": str(state.get("SemanticSourceIdentity", "")),
                    },
                    details={
                        "attempt": attempt,
                        "verdict": str(state.get("LastSemanticVerdict", "")),
                        "source_identity": str(state.get("SemanticSourceIdentity", "")),
                    },
                )
            else:
                _record_incomplete_stage(path, "semantic-verified", outcome, attempt, payload)
            return
        if name == "pr-and-ci":
            if outcome == "CONTINUE":
                run_manifest.complete_stage(
                    path,
                    "pr-created",
                    run_root=current,
                    artifacts=[current / "ci-summary.json"],
                    inputs={
                        "semantic_output": _stage_output_hash(run_manifest.load_manifest(path), "semantic-verified"),
                        "shipped_source_identity": str(state.get("ShippedSourceIdentity", "")),
                    },
                    details={
                        "attempt": attempt,
                        "head_sha": str(state.get("PrHeadSha", "")),
                        "commit_sha": str(state.get("LastCommitSha", "")),
                        "created_tree_sha": str(state.get("CreatedTreeSha", "")),
                        "ci_state": str((state.get("CiProof", {}) or {}).get("state", "")) if isinstance(state.get("CiProof", {}), dict) else "",
                    },
                )
                run_manifest.update_pr(
                    path,
                    number=int(state.get("PrNumber", 0) or 0) or None,
                    url=str(state.get("PrUrl", "")),
                    state="ci-passed",
                )
            else:
                _record_incomplete_stage(path, "pr-created", outcome, attempt, payload)
            return
        if name in {"blocked", "failed"} or outcome in {"BLOCKED", "FAILED"}:
            run_manifest.record_failure(
                path,
                classification=str(payload.get("failure_classification", "workflow_failed")),
                reason=str(payload.get("reason", "OpenCode workflow stopped")),
                stage=str(payload.get("failed_stage", name)),
            )
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc

def checkpoint_failure(repo: Path, stage: str, error: BaseException) -> None:
    path = manifest_path(repo)
    if not path.is_file():
        return
    classification = str(getattr(error, "classification", "") or workflow_stages.FAILURE_DETERMINISTIC)
    try:
        run_manifest.record_failure(path, classification=classification, reason=str(error), stage=stage)
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc

def _record_incomplete_stage(
    path: Path,
    stage: str,
    outcome: str,
    attempt: int,
    payload: dict[str, object],
) -> None:
    status = "repair-required" if outcome == "REPAIR" else outcome.casefold() or "failed"
    effective_attempt = int(payload.get("repair_attempt", attempt) or 0)
    run_manifest.record_stage_state(
        path,
        stage,
        status=status,
        details={
            "attempt": effective_attempt,
            "reason": str(payload.get("reason", "")),
            "failure_classification": str(payload.get("failure_classification", "")),
            "failure_fingerprint": str(payload.get("failure_fingerprint", "")),
            "artifact": str(payload.get("artifact", "")),
        },
    )
    if outcome in {"BLOCKED", "FAILED"}:
        run_manifest.record_failure(
            path,
            classification=str(payload.get("failure_classification", "workflow_failed")),
            reason=str(payload.get("reason", "OpenCode workflow stopped")),
            stage=stage,
        )

def _checkpoint_patch_applied(
    path: Path,
    current: Path,
    proof: dict[str, object],
    *,
    kind: str,
    attempt: int,
) -> None:
    run_manifest.complete_stage(
        path,
        "patch-applied",
        run_root=current,
        inputs={"source_identity": str(proof.get("identity", ""))},
        details={"kind": kind, "attempt": attempt, **_source_details(proof)},
    )

def _source_details(proof: dict[str, object]) -> dict[str, object]:
    changes = proof.get("changes", [])
    return {
        "source_identity": str(proof.get("identity", "")),
        "parent_sha": str(proof.get("parent_sha", "")),
        "changed_paths": [str(item.get("path", "")) for item in changes if isinstance(item, dict) and str(item.get("path", ""))],
    }

def _existing(current: Path, *names: str) -> list[Path]:
    return [current / name for name in names if (current / name).is_file()]

def _stage_record(manifest: dict[str, object], stage: str) -> dict[str, object]:
    stages = manifest.get("stages", {})
    record = stages.get(stage, {}) if isinstance(stages, dict) else {}
    return record if isinstance(record, dict) else {}

def _stage_output_hash(manifest: dict[str, object], stage: str) -> str:
    return str(_stage_record(manifest, stage).get("output_hash", ""))

def _stage_attempt(manifest: dict[str, object], stage: str) -> int:
    record = _stage_record(manifest, stage)
    details = record.get("details", {}) if isinstance(record, dict) else {}
    return int(details.get("attempt", 0) or 0) if isinstance(details, dict) else 0

def _repair_kind(arguments: str) -> str:
    lowered = (arguments or "").casefold()
    if "semantic" in lowered or "verifier" in lowered:
        return "semantic"
    if "ci" in lowered:
        return "ci"
    if "local" in lowered or "deterministic" in lowered:
        return "local"
    return ""

def _stage_for_repair_kind(kind: str) -> str:
    for stage, value in REPAIR_STAGE_KIND.items():
        if value == kind:
            return stage
    raise OpenCodeResumeError(f"unknown OpenCode repair kind: {kind}")

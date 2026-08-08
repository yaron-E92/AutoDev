from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from automation import run_manifest, workflow_stages


ROLE_NAMES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
MODEL_STAGE_ROLE = {
    "repository-read": "reader",
    "handoff-synthesized": "synthesizer",
    "plan-created": "planner",
    "implementation-generated": "implementer",
    "semantic-verified": "verifier",
}
REPAIR_STAGE_KIND = {
    "deterministic-verified": "local",
    "semantic-verified": "semantic",
    "pr-created": "ci",
}
NEXT_ACTION = {
    "issue-selected": "prepare",
    "repository-read": "reader",
    "handoff-synthesized": "synthesizer",
    "plan-created": "planner",
    "implementation-generated": "implementer",
    "patch-applied": "implementer-checkpoint",
    "deterministic-verified": "local-check",
    "semantic-verified": "verifier",
    "pr-created": "pr-and-ci",
}


class OpenCodeResumeError(ValueError):
    pass


def manifest_path(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_stages.CURRENT_DIR / run_manifest.MANIFEST_NAME


def has_manifest(repo: Path) -> bool:
    return manifest_path(repo).is_file()


def create_open_code_manifest(repo: Path, state: dict[str, object]) -> Path:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    path = manifest_path(repo)
    if path.is_file():
        return path
    try:
        run_manifest.create_manifest(
            path,
            repo_path=repo,
            github_repo=str(state.get("RepoFullName", "")),
            issue_number=int(state.get("IssueNumber", 0) or 0),
            mode="issue-to-pr",
            base_sha=str(state.get("BaseSha", "")),
            branch=str(state.get("BranchName", "")),
            role_snapshots={},
            prompt_policy={},
            semantic_verification={"enabled": True, "frontend": "opencode"},
        )
        run_manifest.complete_stage(
            path,
            "issue-selected",
            run_root=current,
            artifacts=[current / "issue.md"],
            inputs={
                "github_repo": str(state.get("RepoFullName", "")),
                "issue_number": int(state.get("IssueNumber", 0) or 0),
                "base_sha": str(state.get("BaseSha", "")),
            },
            details={
                "branch": str(state.get("BranchName", "")),
                "base_tree_sha": str(state.get("BaseTreeSha", "")),
                "prepared_snapshot_hash": str(state.get("PreparedSnapshotHash", "")),
            },
        )
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc
    return path


def role_snapshots(mappings: dict[str, dict[str, str]]) -> dict[str, object]:
    snapshots: dict[str, object] = {}
    for role in ROLE_NAMES:
        mapping = mappings.get(role, {})
        model = str(mapping.get("model", ""))
        provider = model.split("/", 1)[0] if "/" in model else ""
        configured = {
            "transport": "opencode",
            "agent": str(mapping.get("agent", f"autodev-{role}")),
            "model": model,
            "source": str(mapping.get("source", "inherited")),
            "inherits_from": str(mapping.get("inherits_from", "")),
        }
        safe = {
            "transport": "opencode",
            "provider": provider,
            "profile_name": str(mapping.get("source", "inherited")),
            "model": model,
            "agent": configured["agent"],
        }
        snapshots[role] = run_manifest.build_role_snapshot(configured, safe)
    return snapshots


def reconcile_models(
    repo: Path,
    mappings: dict[str, dict[str, str]],
    *,
    invalidated_roles: set[str] | None = None,
) -> dict[str, list[str]]:
    path = manifest_path(repo)
    if not path.is_file():
        raise OpenCodeResumeError(".autodev-run/current/run-manifest.json is missing; this run predates OpenCode resumability")
    try:
        return run_manifest.reconcile_role_snapshots(
            path,
            role_snapshots(mappings),
            explicit_invalidations=invalidated_roles or set(),
        )
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc


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
            run_manifest.record_stage_state(
                path,
                _stage_for_repair_kind(kind),
                status="pending",
                details={"attempt": attempt, "repair_kind": kind},
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


def status_text(
    repo: Path,
    mappings: dict[str, dict[str, str]],
    *,
    requested_invalidations: list[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    path = manifest_path(repo)
    if not path.is_file():
        raise OpenCodeResumeError(".autodev-run/current/run-manifest.json is missing; no #37 OpenCode run is available")
    try:
        manifest = run_manifest.load_manifest(path)
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc
    state = workflow_stages.read_state(current)
    problems = _resume_problems(repo, current, manifest, state, runner=runner, validate_remote=False)
    action = resume_action(manifest, state)
    role = _role_for_action(action)
    mapping = mappings.get(role, {}) if role else {}
    target = manifest.get("target", {}) if isinstance(manifest.get("target", {}), dict) else {}
    failure = manifest.get("failure", {}) if isinstance(manifest.get("failure", {}), dict) else {}
    changed = _changed_role_consequences(manifest, mappings)
    requested_invalidations = requested_invalidations or []

    lines = [
        f"Issue: #{target.get('issue_number', state.get('IssueNumber', 0))}",
        f"Repository: {target.get('github_repo', state.get('RepoFullName', ''))}",
        f"Branch: {target.get('branch', state.get('BranchName', ''))}",
        f"Run ID: {manifest.get('run_id', '')}",
        f"Run directory: {current}",
        "Completed stages: " + (", ".join(str(value) for value in manifest.get("completed_stages", [])) or "(none)"),
        f"Current/failed stage: {failure.get('stage') or manifest.get('current_stage', '')}",
        f"Next valid action: {action}",
        f"Last failure: {failure.get('classification', '') or '(none)'}" + (f" — {failure.get('reason')}" if failure.get("reason") else ""),
        f"Safely resumable: {'no' if problems else 'yes'}",
        f"Working-tree drift detected: {'yes' if any('worktree' in value.casefold() or 'source' in value.casefold() or 'head' in value.casefold() for value in problems) else 'no'}",
        f"Commit: {state.get('LastCommitSha', '') or '(none)'}",
        f"PR: {state.get('PrUrl', '') or '(none)'}",
    ]
    if role:
        model = str(mapping.get("model", ""))
        inheritance = str(mapping.get("inherits_from", ""))
        resolution = model or (f"inherited from {inheritance}" if inheritance else "OpenCode current/default")
        lines.append(f"Next model role: {role} -> {resolution}")
    if changed:
        lines.append("Changed role configuration:")
        for changed_role, affected in sorted(changed.items()):
            lines.append(
                f"  {changed_role}: " + (", ".join(affected) + " require explicit invalidation" if affected else "future stage only; safe")
            )
    if requested_invalidations:
        lines.append("Requested invalidation preview:")
        for requested in requested_invalidations:
            affected = run_manifest.invalidated_stages_for_role(manifest, requested)
            lines.append(f"  {requested}: " + (", ".join(affected) if affected else "no completed stages"))
    if problems:
        lines.append("Resume blockers:")
        lines.extend(f"  - {problem}" for problem in problems)
    attempts = repair_attempts(manifest)
    lines.append(
        f"Repair counters: local={attempts['local']} semantic={attempts['semantic']} ci={attempts['ci']}"
    )
    return "\n".join(lines) + "\n"


def resume(
    repo: Path,
    mappings: dict[str, dict[str, str]],
    *,
    invalidated_roles: set[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    path = manifest_path(repo)
    if not path.is_file():
        raise OpenCodeResumeError(".autodev-run/current/run-manifest.json is missing; this run cannot be resumed through #37")
    try:
        manifest = run_manifest.load_manifest(path)
        invalidated_roles = invalidated_roles or set()
        for role in invalidated_roles:
            affected = run_manifest.invalidated_stages_for_role(manifest, role)
            if "patch-applied" in affected:
                state = workflow_stages.read_state(current)
                if workflow_stages.workspace_changes(repo, current, state):
                    raise OpenCodeResumeError(
                        f"cannot invalidate completed {role} work while direct OpenCode edits remain in the worktree; restore the prepared base first"
                    )
        run_manifest.reconcile_role_snapshots(
            path,
            role_snapshots(mappings),
            explicit_invalidations=invalidated_roles,
        )
        manifest = run_manifest.load_manifest(path)
        state = workflow_stages.read_state(current)
        _repair_atomic_implementation_checkpoint(repo, current, path, manifest, state)
        manifest = run_manifest.load_manifest(path)
        problems = _resume_problems(repo, current, manifest, state, runner=runner, validate_remote=True)
        if problems:
            raise OpenCodeResumeError("resume refused: " + "; ".join(problems))
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc

    action = resume_action(manifest, state)
    role = _role_for_action(action)
    mapping = mappings.get(role, {}) if role else {}
    attempts = repair_attempts(manifest)
    target = manifest.get("target", {}) if isinstance(manifest.get("target", {}), dict) else {}
    return {
        "state": "COMPLETE" if action == "complete" else "RESUME",
        "issue_number": int(target.get("issue_number", state.get("IssueNumber", 0)) or 0),
        "branch": str(target.get("branch", state.get("BranchName", ""))),
        "run_id": str(manifest.get("run_id", "")),
        "run_dir": str(current),
        "next_stage": run_manifest.next_stage(manifest),
        "next_action": action,
        "next_role": role,
        "next_model": str(mapping.get("model", "")) if role else "",
        "model_source": str(mapping.get("source", "")) if role else "",
        "local_repair_attempt": attempts["local"],
        "semantic_repair_attempt": attempts["semantic"],
        "ci_repair_attempt": attempts["ci"],
        "commit_sha": str(state.get("LastCommitSha", "")),
        "pr_url": str(state.get("PrUrl", "")),
    }


def resume_action(manifest: dict[str, object], state: dict[str, object]) -> str:
    for stage, kind in REPAIR_STAGE_KIND.items():
        record = _stage_record(manifest, stage)
        status = str(record.get("status", "")) if isinstance(record, dict) else ""
        if status in {"repair-required", "repair-in-progress"}:
            return f"fixer-{kind}"
    repair_record = _stage_record(manifest, "repair-generated")
    if isinstance(repair_record, dict) and str(repair_record.get("status", "")) == "in-progress":
        details = repair_record.get("details", {})
        kind = str(details.get("kind", "")) if isinstance(details, dict) else ""
        if kind:
            return f"fixer-{kind}"
    stage = run_manifest.next_stage(manifest)
    if stage == "complete":
        return "complete" if str(state.get("Status", "")) == "ReadyForReview" else "ready"
    return NEXT_ACTION.get(stage, stage)


def repair_attempts(manifest: dict[str, object]) -> dict[str, int]:
    return {
        kind: _stage_attempt(manifest, stage)
        for stage, kind in REPAIR_STAGE_KIND.items()
    }


def _record_incomplete_stage(
    path: Path,
    stage: str,
    outcome: str,
    attempt: int,
    payload: dict[str, object],
) -> None:
    status = "repair-required" if outcome == "REPAIR" else outcome.casefold() or "failed"
    run_manifest.record_stage_state(
        path,
        stage,
        status=status,
        details={
            "attempt": attempt,
            "reason": str(payload.get("reason", "")),
            "failure_classification": str(payload.get("failure_classification", "")),
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


def _repair_atomic_implementation_checkpoint(
    repo: Path,
    current: Path,
    path: Path,
    manifest: dict[str, object],
    state: dict[str, object],
) -> None:
    if not run_manifest.stage_completed(manifest, "implementation-generated") or run_manifest.stage_completed(manifest, "patch-applied"):
        return
    implementation = _stage_record(manifest, "implementation-generated")
    details = implementation.get("details", {}) if isinstance(implementation, dict) else {}
    expected = str(details.get("source_identity", "")) if isinstance(details, dict) else ""
    proof = workflow_stages.source_identity(repo, current, state)
    if not expected or str(proof.get("identity", "")) != expected:
        raise OpenCodeResumeError("implementation checkpoint is incomplete and the current worktree no longer matches the accepted implementation")
    _checkpoint_patch_applied(path, current, proof, kind="implementation", attempt=0)


def _resume_problems(
    repo: Path,
    current: Path,
    manifest: dict[str, object],
    state: dict[str, object],
    *,
    runner: Callable[..., object],
    validate_remote: bool,
) -> list[str]:
    problems = list(run_manifest.validate_artifacts(manifest, current))
    target = manifest.get("target", {})
    if not isinstance(target, dict):
        return [*problems, "run manifest target is invalid"]
    if str(Path(str(target.get("repo_path", ""))).resolve()) != str(repo.resolve()):
        problems.append("repository path does not match the run manifest")
    expected_pairs = (
        ("github_repo", "RepoFullName"),
        ("issue_number", "IssueNumber"),
        ("base_sha", "BaseSha"),
        ("branch", "BranchName"),
    )
    for manifest_key, state_key in expected_pairs:
        if str(target.get(manifest_key, "")) != str(state.get(state_key, "")):
            problems.append(f"{manifest_key} does not match state.json")
    try:
        head = str(getattr(workflow_stages.git(repo, ["rev-parse", "HEAD"], runner=runner), "stdout", "") or "").strip()
    except workflow_stages.WorkflowStageError as exc:
        problems.append(str(exc))
        head = ""
    if head and head != str(target.get("base_sha", "")):
        problems.append(f"local HEAD {head} no longer matches prepared base {target.get('base_sha', '')}")

    if run_manifest.stage_completed(manifest, "pr-created"):
        pr_record = _stage_record(manifest, "pr-created")
        details = pr_record.get("details", {}) if isinstance(pr_record, dict) else {}
        expected_head = str(details.get("head_sha", "")) if isinstance(details, dict) else ""
        if expected_head != str(state.get("PrHeadSha", "")):
            problems.append("PR head proof does not match the completed pr-created checkpoint")
        ci = state.get("CiProof", {})
        if not isinstance(ci, dict) or ci.get("state") != "terminal-success" or str(ci.get("head_sha", "")) != expected_head:
            problems.append("terminal-success CI proof for the checkpointed PR head is missing or stale")
        try:
            if workflow_stages.workspace_changes(repo, current, state):
                problems.append("worktree changed after the shipped commit checkpoint")
        except workflow_stages.WorkflowStageError as exc:
            problems.append(str(exc))
        if validate_remote and not problems:
            try:
                workflow_stages.validate_ready_proof(current, state, runner=runner)
            except workflow_stages.WorkflowStageError as exc:
                problems.append(str(exc))
        return problems

    if run_manifest.stage_completed(manifest, "patch-applied"):
        patch_record = _stage_record(manifest, "patch-applied")
        details = patch_record.get("details", {}) if isinstance(patch_record, dict) else {}
        expected_identity = str(details.get("source_identity", "")) if isinstance(details, dict) else ""
        try:
            actual = workflow_stages.source_identity(repo, current, state)
            if not expected_identity or str(actual.get("identity", "")) != expected_identity:
                problems.append("source/worktree drift detected after the patch-applied checkpoint")
        except workflow_stages.WorkflowStageError as exc:
            problems.append(str(exc))
    else:
        try:
            if workflow_stages.workspace_changes(repo, current, state):
                problems.append("worktree changed before the patch-applied checkpoint")
        except workflow_stages.WorkflowStageError as exc:
            problems.append(str(exc))
    return problems


def _changed_role_consequences(
    manifest: dict[str, object],
    mappings: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    current = role_snapshots(mappings)
    existing = manifest.get("roles", {})
    if not isinstance(existing, dict):
        existing = {}
    changed: dict[str, list[str]] = {}
    for role in ROLE_NAMES:
        previous = existing.get(role)
        latest = current.get(role)
        previous_fingerprint = previous.get("fingerprint") if isinstance(previous, dict) else ""
        latest_fingerprint = latest.get("fingerprint") if isinstance(latest, dict) else ""
        if previous_fingerprint and latest_fingerprint != previous_fingerprint:
            changed[role] = run_manifest.invalidated_stages_for_role(manifest, role)
    return changed


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


def _role_for_action(action: str) -> str:
    if action.startswith("fixer-"):
        return "fixer"
    return {
        "reader": "reader",
        "synthesizer": "synthesizer",
        "planner": "planner",
        "implementer": "implementer",
        "verifier": "verifier",
    }.get(action, "")

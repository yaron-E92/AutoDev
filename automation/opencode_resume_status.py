from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable
from automation import run_manifest, workflow_stages

from automation.opencode_resume_checkpoint import (
    _stage_attempt,
    _stage_record,
)
from automation.opencode_resume_contract import (
    NEXT_ACTION,
    OpenCodeResumeError,
    REPAIR_STAGE_KIND,
    ROLE_NAMES,
    manifest_path,
)
from automation.opencode_resume_manifest import (
    role_snapshots,
)

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

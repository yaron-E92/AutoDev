from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable
from automation import run_manifest, workflow_stages, ux_resolver, ux_workflow

from automation.opencode_resume_checkpoint import (
    _checkpoint_patch_applied,
    _stage_record,
)
from automation.opencode_resume_contract import (
    OpenCodeResumeError,
    manifest_path,
)
from automation.opencode_resume_manifest import (
    role_snapshots,
)
from automation.opencode_resume_status import (
    _resume_problems,
    _role_for_action,
    repair_attempts,
    resume_action,
)

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
        try:
            ux_workflow.validate_resume_identity(
                repo,
                manifest.get("ux_artifact", {}),
            )
        except ux_resolver.UXResolutionError as exc:
            raise OpenCodeResumeError(str(exc)) from exc
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

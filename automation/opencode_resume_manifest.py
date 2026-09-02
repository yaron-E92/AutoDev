from __future__ import annotations

from pathlib import Path
from automation import run_manifest, workflow_stages

from automation.opencode_resume_contract import (
    OpenCodeResumeError,
    ROLE_NAMES,
    manifest_path,
)

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
            ux_artifact=dict(state.get("UXArtifact", {})) if isinstance(state.get("UXArtifact", {}), dict) else {},
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
                "ux_immutable_identity": str((state.get("UXArtifact", {}) if isinstance(state.get("UXArtifact", {}), dict) else {}).get("immutable_identity", "")),
            },
            details={
                "branch": str(state.get("BranchName", "")),
                "base_tree_sha": str(state.get("BaseTreeSha", "")),
                "prepared_snapshot_hash": str(state.get("PreparedSnapshotHash", "")),
                "ux_artifact": dict(state.get("UXArtifact", {})) if isinstance(state.get("UXArtifact", {}), dict) else {},
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

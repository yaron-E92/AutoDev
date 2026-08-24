from __future__ import annotations

from automation import opencode_resume_status

from automation import opencode_resume_execution

from automation import opencode_resume_contract

from automation import opencode_resume_checkpoint

import subprocess
from pathlib import Path
from typing import Callable

from automation import run_manifest, workflow_stages


class RoleResumeError(ValueError):
    pass


def manifest_path(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_stages.CURRENT_DIR / run_manifest.MANIFEST_NAME


def has_manifest(repo: Path) -> bool:
    return manifest_path(repo).is_file()


def create_manifest(
    repo: Path,
    state: dict[str, object],
    *,
    runtime_name: str,
) -> Path:
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
            semantic_verification={"enabled": True, "frontend": runtime_name},
        )
        manifest = run_manifest.load_manifest(path)
        manifest["role_runtime"] = {"name": runtime_name}
        run_manifest.save_manifest(path, manifest)
        run_manifest.complete_stage(
            path,
            "issue-selected",
            run_root=current,
            artifacts=[current / "issue.md"],
            inputs={
                "github_repo": str(state.get("RepoFullName", "")),
                "issue_number": int(state.get("IssueNumber", 0) or 0),
                "base_sha": str(state.get("BaseSha", "")),
                "role_runtime": runtime_name,
            },
            details={
                "branch": str(state.get("BranchName", "")),
                "base_tree_sha": str(state.get("BaseTreeSha", "")),
                "prepared_snapshot_hash": str(state.get("PreparedSnapshotHash", "")),
                "role_runtime": runtime_name,
            },
        )
    except run_manifest.ManifestError as exc:
        raise RoleResumeError(str(exc)) from exc
    return path


def begin_role(repo: Path, role: str, arguments: str) -> None:
    try:
        opencode_resume_checkpoint.begin_role(repo, role, arguments)
    except opencode_resume_contract.OpenCodeResumeError as exc:
        raise RoleResumeError(str(exc)) from exc


def reconcile_snapshots(
    repo: Path,
    snapshots: dict[str, object],
    *,
    invalidated_roles: set[str] | None = None,
) -> dict[str, list[str]]:
    path = manifest_path(repo)
    if not path.is_file():
        raise RoleResumeError(
            ".autodev-run/current/run-manifest.json is missing; this run cannot be resumed"
        )
    try:
        return run_manifest.reconcile_role_snapshots(
            path,
            snapshots,
            explicit_invalidations=invalidated_roles or set(),
        )
    except run_manifest.ManifestError as exc:
        raise RoleResumeError(str(exc)) from exc


def checkpoint_role(
    repo: Path,
    role: str,
    outputs: list[Path],
    snapshots: dict[str, object],
    *,
    runtime_name: str,
) -> None:
    repo = repo.expanduser().resolve()
    path = manifest_path(repo)
    if not path.is_file():
        return
    current = repo / workflow_stages.CURRENT_DIR
    reconcile_snapshots(repo, snapshots)
    manifest = run_manifest.load_manifest(path)
    try:
        if role == "reader":
            artifacts = opencode_resume_checkpoint._existing(
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
                    "repository_read_output": opencode_resume_checkpoint._stage_output_hash(manifest, "repository-read"),
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
                    "handoff_output": opencode_resume_checkpoint._stage_output_hash(manifest, "handoff-synthesized"),
                    "planner_fingerprint": run_manifest.stage_role_fingerprint(manifest, "planner"),
                },
            )
            return
        if role == "implementer":
            proof = workflow_stages.source_identity(
                repo,
                current,
                workflow_stages.read_state(current),
            )
            run_manifest.complete_stage(
                path,
                "implementation-generated",
                run_root=current,
                artifacts=[current / "commit-message.txt"],
                inputs={
                    "plan_output": opencode_resume_checkpoint._stage_output_hash(manifest, "plan-created"),
                    "implementer_fingerprint": run_manifest.stage_role_fingerprint(manifest, "implementer"),
                },
                details=opencode_resume_checkpoint._source_details(proof),
            )
            opencode_resume_checkpoint._checkpoint_patch_applied(
                path,
                current,
                proof,
                kind="implementation",
                attempt=0,
            )
            return
        if role == "fixer":
            manifest = run_manifest.load_manifest(path)
            repair = opencode_resume_checkpoint._stage_record(manifest, "repair-generated")
            details = repair.get("details", {}) if isinstance(repair, dict) else {}
            kind = str(details.get("kind", "")) if isinstance(details, dict) else ""
            attempt = int(details.get("attempt", 0) or 0) if isinstance(details, dict) else 0
            if not kind:
                raise RoleResumeError(
                    "fixer completion has no durable repair kind in the run manifest"
                )
            run_manifest.invalidate_role(
                path,
                "fixer",
                reason=f"{runtime_name} {kind} repair applied",
            )
            proof = workflow_stages.source_identity(
                repo,
                current,
                workflow_stages.read_state(current),
            )
            run_manifest.complete_stage(
                path,
                "repair-generated",
                run_root=current,
                inputs={
                    "fixer_fingerprint": run_manifest.stage_role_fingerprint(
                        run_manifest.load_manifest(path),
                        "fixer",
                    ),
                    "kind": kind,
                    "attempt": attempt,
                },
                details={
                    "kind": kind,
                    "attempt": attempt,
                    **opencode_resume_checkpoint._source_details(proof),
                },
            )
            opencode_resume_checkpoint._checkpoint_patch_applied(
                path,
                current,
                proof,
                kind=kind,
                attempt=attempt,
            )
            run_manifest.record_stage_state(
                path,
                opencode_resume_checkpoint._stage_for_repair_kind(kind),
                status="pending",
                details={"attempt": attempt, "repair_kind": kind},
            )
            return
        if role == "verifier":
            return
    except (run_manifest.ManifestError, workflow_stages.WorkflowStageError) as exc:
        raise RoleResumeError(str(exc)) from exc


def checkpoint_stage(repo: Path, name: str, payload: dict[str, object], attempt: int) -> None:
    try:
        opencode_resume_checkpoint.checkpoint_stage(repo, name, payload, attempt)
    except opencode_resume_contract.OpenCodeResumeError as exc:
        raise RoleResumeError(str(exc)) from exc


def checkpoint_failure(repo: Path, stage: str, error: BaseException) -> None:
    try:
        opencode_resume_checkpoint.checkpoint_failure(repo, stage, error)
    except opencode_resume_contract.OpenCodeResumeError as exc:
        raise RoleResumeError(str(exc)) from exc


def resume(
    repo: Path,
    snapshots: dict[str, object],
    *,
    invalidated_roles: set[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    path = manifest_path(repo)
    if not path.is_file():
        raise RoleResumeError(
            ".autodev-run/current/run-manifest.json is missing; this run cannot be resumed"
        )
    try:
        manifest = run_manifest.load_manifest(path)
        invalidated_roles = invalidated_roles or set()
        for role in invalidated_roles:
            affected = run_manifest.invalidated_stages_for_role(manifest, role)
            if "patch-applied" in affected:
                state = workflow_stages.read_state(current)
                if workflow_stages.workspace_changes(repo, current, state):
                    raise RoleResumeError(
                        f"cannot invalidate completed {role} work while direct runtime edits remain in the worktree; restore the prepared base first"
                    )
        run_manifest.reconcile_role_snapshots(
            path,
            snapshots,
            explicit_invalidations=invalidated_roles,
        )
        manifest = run_manifest.load_manifest(path)
        state = workflow_stages.read_state(current)
        opencode_resume_execution._repair_atomic_implementation_checkpoint(
            repo,
            current,
            path,
            manifest,
            state,
        )
        manifest = run_manifest.load_manifest(path)
        problems = opencode_resume_status._resume_problems(
            repo,
            current,
            manifest,
            state,
            runner=runner,
            validate_remote=True,
        )
        if problems:
            raise RoleResumeError("resume refused: " + "; ".join(problems))
    except run_manifest.ManifestError as exc:
        raise RoleResumeError(str(exc)) from exc

    action = opencode_resume_status.resume_action(manifest, state)
    role = opencode_resume_status._role_for_action(action)
    attempts = opencode_resume_status.repair_attempts(manifest)
    target = manifest.get("target", {}) if isinstance(manifest.get("target", {}), dict) else {}
    snapshot = snapshots.get(role, {}) if role else {}
    safe = snapshot.get("safe_metadata", {}) if isinstance(snapshot, dict) else {}
    safe = safe if isinstance(safe, dict) else {}
    return {
        "state": "COMPLETE" if action == "complete" else "RESUME",
        "issue_number": int(target.get("issue_number", state.get("IssueNumber", 0)) or 0),
        "branch": str(target.get("branch", state.get("BranchName", ""))),
        "run_id": str(manifest.get("run_id", "")),
        "run_dir": str(current),
        "next_stage": run_manifest.next_stage(manifest),
        "next_action": action,
        "next_role": role,
        "next_model": str(safe.get("model", "")) if role else "",
        "runtime": str(safe.get("runtime", safe.get("transport", ""))) if role else "",
        "local_repair_attempt": attempts["local"],
        "semantic_repair_attempt": attempts["semantic"],
        "ci_repair_attempt": attempts["ci"],
        "commit_sha": str(state.get("LastCommitSha", "")),
        "pr_url": str(state.get("PrUrl", "")),
    }

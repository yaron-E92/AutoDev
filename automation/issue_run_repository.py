from __future__ import annotations

import json
import os
import re
import shutil
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TextIO
from automation import run_real_issue_core as _core
from automation.run_real_issue_core import *  # noqa: F401,F403
from automation.model_providers import ModelConfig, ModelProvider, ProviderResponse, create_provider, load_provider_config
from automation.model_roles import (
    MODEL_ROLES,
    ModelInvocationError,
    append_invocation_metadata,
    invoke_model,
    model_config_to_dict,
    resolve_role_configs,
    safe_role_metadata,
)
from automation.prompt_policies import (
    compose_prompt,
    resolve_prompt_policies,
    role_policy_metadata,
    safe_prompt_policy_metadata,
)
from automation.run_manifest import (
    MANIFEST_NAME,
    ManifestError,
    build_role_snapshot,
    complete_stage,
    create_manifest,
    hash_file,
    hash_text,
    load_manifest,
    manifest_path,
    next_stage,
    reconcile_role_snapshots,
    record_failure,
    record_stage_state,
    render_status,
    save_manifest,
    stage_completed,
    stage_role_fingerprint,
    sync_invocations,
    update_pr,
    validate_artifacts,
)
from automation.semantic_verifier import (
    SemanticSettings,
    SemanticVerifierError,
    build_schema_repair_prompt,
    build_semantic_prompt,
    build_semantic_repair_prompt,
    collect_changed_files,
    collect_current_diff,
    parse_semantic_output,
    resolve_semantic_settings,
    safe_semantic_metadata,
    write_final_verdict,
    write_semantic_result,
)
from automation.issue_run_resume import (
    _update_resume_target_options,
)
from automation.issue_run_session import (
    _ACTIVE_RESUMING,
    _ACTIVE_ROLE_SNAPSHOTS,
    _CORE_ENSURE_CLEAN_WORKTREE,
    _CORE_ENSURE_ISSUE_BRANCH,
    _CORE_FETCH_ISSUE_TEXT,
    _CORE_SELECT_ISSUE,
    _active_args,
    _active_manifest_data,
    _active_manifest_path,
    _stage_details,
)

def update_issue_labels(repo, github_repo, issue, *, add, remove, stream):
    for label in add:
        run_command(
            ["gh", "issue", "edit", str(issue), "--repo", github_repo, "--add-label", label],
            cwd=repo,
            stream=stream,
        )
    for label in remove:
        run_command(
            ["gh", "issue", "edit", str(issue), "--repo", github_repo, "--remove-label", label],
            cwd=repo,
            stream=stream,
        )

def select_issue(args, repo, stream):
    if not _ACTIVE_RESUMING.get():
        return _CORE_SELECT_ISSUE(args, repo, stream)
    manifest = _active_manifest_data()
    target = manifest["target"]
    selected = read_json(Path(args.out) / "selected-issue.json")  # noqa: F405
    return IssueSelection(  # noqa: F405
        number=int(target["issue_number"]),
        title=str(selected.get("title", "")) if isinstance(selected, dict) else "",
        url=str(selected.get("url", "")) if isinstance(selected, dict) else "",
        labels=list(selected.get("labels", [])) if isinstance(selected, dict) and isinstance(selected.get("labels"), list) else [],
        body=str(selected.get("body", "")) if isinstance(selected, dict) else "",
    )

def fetch_issue_text(github_repo, issue, repo, stream):
    if _ACTIVE_RESUMING.get():
        issue_path = Path(_active_args().out) / "issue.md"
        try:
            return issue_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RunnerError(f"resume issue artifact is missing: {issue_path}", 2) from exc  # noqa: F405

    issue_text = _CORE_FETCH_ISSUE_TEXT(github_repo, issue, repo, stream)
    path = _active_manifest_path()
    if not path.is_file():
        base_sha = run_command(["git", "rev-parse", "HEAD"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
        branch = issue_branch_name(issue, issue_text)  # noqa: F405
        metadata = read_json(Path(_active_args().out) / "provider-metadata.json")  # noqa: F405
        prompt_policy = metadata.get("prompt_policy", {}) if isinstance(metadata, dict) else {}
        semantic = metadata.get("semantic_verification", {}) if isinstance(metadata, dict) else {}
        create_manifest(
            path,
            repo_path=repo,
            github_repo=github_repo,
            issue_number=issue,
            mode=_active_args().mode,
            base_sha=base_sha,
            branch=branch,
            role_snapshots=_ACTIVE_ROLE_SNAPSHOTS.get() or {},
            prompt_policy=prompt_policy if isinstance(prompt_policy, dict) else {},
            semantic_verification=semantic if isinstance(semantic, dict) else {},
        )
        _update_resume_target_options(path, _active_args())
    return issue_text

def ensure_clean_worktree(repo, stream):
    if not _ACTIVE_RESUMING.get():
        return _CORE_ENSURE_CLEAN_WORKTREE(repo, stream)
    _validate_resume_repository(repo, stream)

def ensure_issue_branch(repo, branch_name, stream):
    path = _active_manifest_path()
    if _ACTIVE_RESUMING.get():
        current = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
        if current != branch_name:
            raise RunnerError(
                f"resume requires branch '{branch_name}', but current branch is '{current}'",
                2,
            )  # noqa: F405
    else:
        _CORE_ENSURE_ISSUE_BRANCH(repo, branch_name, stream)
    manifest = load_manifest(path)
    if not stage_completed(manifest, "issue-selected"):
        out_dir = Path(_active_args().out)
        complete_stage(
            path,
            "issue-selected",
            run_root=out_dir,
            artifacts=[out_dir / "selected-issue.json", out_dir / "issue.md"],
            inputs={
                "github_repo": _active_args().github_repo,
                "issue_number": _active_args().issue,
                "base_sha": manifest["target"]["base_sha"],
            },
            details={"branch": branch_name},
        )

def _validate_resume_repository(repo: Path, stream: TextIO) -> None:
    manifest = _active_manifest_data()
    target = manifest.get("target", {})
    if not isinstance(target, dict):
        raise RunnerError("resume manifest target is invalid", 2)  # noqa: F405
    if str(repo.resolve()) != str(Path(str(target["repo_path"])).resolve()):
        raise RunnerError("resume repository path does not match the manifest", 2)  # noqa: F405
    problems = validate_artifacts(manifest, Path(_active_args().out))
    if problems:
        raise RunnerError("resume artifact validation failed: " + "; ".join(problems), 2)  # noqa: F405

    current_branch = run_command(["git", "branch", "--show-current"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
    if current_branch != target["branch"]:
        raise RunnerError(
            f"resume branch mismatch: expected {target['branch']}, found {current_branch}",
            2,
        )  # noqa: F405
    base_sha = str(target["base_sha"])
    ancestry = run_command(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=repo,
        stream=stream,
        check=False,
    )  # noqa: F405
    if ancestry.returncode != 0:
        raise RunnerError("resume refused because the branch no longer descends from the original base SHA", 2)  # noqa: F405

    current_head = run_command(["git", "rev-parse", "HEAD"], cwd=repo, stream=stream).stdout.strip()  # noqa: F405
    status_paths = sorted(changed_worktree_paths(repo, stream))  # noqa: F405
    if stage_completed(manifest, "pr-created"):
        expected_head = str(_stage_details(manifest, "pr-created").get("head_sha", ""))
        if expected_head and current_head != expected_head:
            raise RunnerError("resume refused because the PR branch head changed after PR creation", 2)  # noqa: F405
        if status_paths:
            raise RunnerError("resume refused because the working tree changed after PR creation", 2)  # noqa: F405
        return

    diff_text = run_command(["git", "diff", "--binary", "HEAD"], cwd=repo, stream=stream, check=False).stdout  # noqa: F405
    actual_hash = hash_text(diff_text)
    if stage_completed(manifest, "patch-applied"):
        patch_details = _stage_details(manifest, "patch-applied")
        expected_hash = str(patch_details.get("worktree_hash", ""))
        expected_paths = sorted(str(path) for path in patch_details.get("changed_paths", []) if str(path))
        if expected_hash == actual_hash and expected_paths == status_paths:
            return
        pending_patch = _pending_uncheckpointed_patch(manifest, Path(_active_args().out))
        if pending_patch is not None and _patch_matches_resume_worktree(
            repo,
            pending_patch,
            status_paths,
            expected_paths,
            stream,
        ):
            return
        if not diff_text and not status_paths and current_head != base_sha:
            committed_diff = run_command(
                ["git", "diff", "--binary", base_sha, "HEAD"],
                cwd=repo,
                stream=stream,
                check=False,
            ).stdout  # noqa: F405
            if (
                hash_text(committed_diff) == expected_hash
                and _is_expected_autodev_commit(repo, stream, int(target["issue_number"]))
            ):
                return
        raise RunnerError("resume refused because the working tree changed after the recorded patch", 2)  # noqa: F405

    pending_patch = _pending_uncheckpointed_patch(manifest, Path(_active_args().out))
    if pending_patch is not None and _patch_matches_resume_worktree(
        repo,
        pending_patch,
        status_paths,
        [],
        stream,
    ):
        return
    if status_paths:
        raise RunnerError("resume refused because the working tree changed before patch application", 2)  # noqa: F405

def _pending_uncheckpointed_patch(manifest: dict[str, object], out_dir: Path) -> Path | None:
    applied_hash = str(_stage_details(manifest, "patch-applied").get("last_patch_hash", "")) if stage_completed(manifest, "patch-applied") else ""
    for stage in ("repair-generated", "implementation-generated"):
        if not stage_completed(manifest, stage):
            continue
        details = _stage_details(manifest, stage)
        relative = str(details.get("patch_path", ""))
        expected_hash = str(details.get("patch_hash", ""))
        if not relative or not expected_hash or expected_hash == applied_hash:
            continue
        path = out_dir / relative
        if path.is_file() and hash_file(path) == expected_hash:
            return path
    return None

def _patch_matches_resume_worktree(
    repo: Path,
    patch: Path,
    status_paths: list[str],
    prior_paths: list[str],
    stream: TextIO,
) -> bool:
    reverse = run_command(
        ["git", "apply", "--check", "--reverse", str(patch)],
        cwd=repo,
        stream=stream,
        check=False,
    )  # noqa: F405
    if reverse.returncode != 0:
        return False
    patch_paths = _patch_paths(repo, patch, stream)
    if not patch_paths:
        return False
    expected_paths = sorted(set(prior_paths) | set(patch_paths))
    return expected_paths == sorted(status_paths)

def _patch_paths(repo: Path, patch: Path, stream: TextIO) -> list[str]:
    result = run_command(
        ["git", "apply", "--numstat", str(patch)],
        cwd=repo,
        stream=stream,
        check=False,
    )  # noqa: F405
    if result.returncode != 0:
        return []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[2].strip():
            paths.append(parts[2].strip())
    return sorted(set(paths))

def _is_expected_autodev_commit(repo: Path, stream: TextIO, issue: int) -> bool:
    subject = run_command(["git", "log", "-1", "--pretty=%s"], cwd=repo, stream=stream, check=False).stdout.strip()  # noqa: F405
    return subject == f"Implement issue {issue} with AutoDev runner"

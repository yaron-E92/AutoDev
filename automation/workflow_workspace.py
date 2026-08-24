from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template
from automation import workspace_scope
from automation.workflow_commands import (
    _decoded_text,
    _porcelain_paths,
    git,
)
from automation.workflow_contract import (
    IGNORED_PREFIXES,
    WorkflowStageError,
)
from automation.workflow_storage import (
    _file_sha256,
    read_json,
    write_json,
)

def validate_prepared_worktree(
    repo: Path,
    base_sha: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    head = _decoded_text(
        getattr(git(repo, ["rev-parse", "HEAD"], runner=runner), "stdout", "")
    ).strip()
    if not head:
        raise WorkflowStageError("could not resolve local HEAD while preparing the run")
    if head != base_sha:
        raise WorkflowStageError(
            f"prepared local HEAD {head} does not match remote base {base_sha}; update/switch to the exact base before starting AutoDev"
        )
    status = _decoded_text(
        getattr(
            git(
                repo,
                ["status", "--porcelain=v1", "--untracked-files=all"],
                runner=runner,
            ),
            "stdout",
            "",
        )
    )
    dirty = [path for path in _porcelain_paths(status) if not ignored_workspace_path(path)]
    if dirty:
        raise WorkflowStageError(
            "prepared worktree is not clean; AutoDev cannot bind verification to the remote base while these paths differ: "
            + ", ".join(dirty[:20])
        )
    return head

def source_identity(repo: Path, current: Path, state: dict[str, object]) -> dict[str, object]:
    parent_sha = str(state.get("LastCommitSha", "")).strip() or str(state.get("BaseSha", "")).strip()
    if not parent_sha:
        raise WorkflowStageError("cannot calculate source identity because the current commit parent is missing")
    baseline_path, expected_hash = _baseline_snapshot(current, state)
    baseline = read_json(baseline_path)
    if not isinstance(baseline, dict):
        raise WorkflowStageError(f"workspace snapshot is missing or invalid: {baseline_path}")
    if expected_hash and _file_sha256(baseline_path) != expected_hash:
        raise WorkflowStageError(
            f"workspace baseline proof changed unexpectedly: {baseline_path}"
        )
    actual = workspace_snapshot(repo)
    changes: list[dict[str, str]] = []
    for path, digest in actual.items():
        if path not in baseline:
            changes.append({"path": path, "status": "added", "sha256": digest})
        elif str(baseline[path]) != digest:
            changes.append({"path": path, "status": "modified", "sha256": digest})
    for path in baseline:
        if path not in actual:
            changes.append({"path": str(path), "status": "deleted", "sha256": ""})
    changes.sort(key=lambda item: item["path"])
    identity = hashlib.sha256(
        json.dumps(
            {"parent_sha": parent_sha, "changes": changes},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="replace")
    ).hexdigest()
    return {"parent_sha": parent_sha, "identity": identity, "changes": changes}

def workspace_changes(repo: Path, current: Path, state: dict[str, object]) -> list[dict[str, str]]:
    baseline_path, expected_hash = _baseline_snapshot(current, state)
    baseline = read_json(baseline_path)
    if not isinstance(baseline, dict):
        raise WorkflowStageError(f"workspace snapshot is missing or invalid: {baseline_path}")
    if state.get("VerificationProofVersion") and expected_hash and _file_sha256(baseline_path) != expected_hash:
        raise WorkflowStageError(f"workspace snapshot proof changed unexpectedly: {baseline_path}")
    actual = workspace_snapshot(repo)
    changes: list[dict[str, str]] = []
    for path, digest in actual.items():
        if path not in baseline:
            changes.append({"Path": path, "Status": "added"})
        elif str(baseline[path]) != digest:
            changes.append({"Path": path, "Status": "modified"})
    for path in baseline:
        if path not in actual:
            changes.append({"Path": str(path), "Status": "deleted"})
    return sorted(changes, key=lambda item: item["Path"])

def _baseline_snapshot(current: Path, state: dict[str, object]) -> tuple[Path, str]:
    if str(state.get("LastCommitSha", "")).strip():
        path = current / "last-commit-workspace-snapshot.json"
        if state.get("VerificationProofVersion") and not path.is_file():
            raise WorkflowStageError("last committed workspace snapshot is missing; cannot bind repair verification to its parent")
        return path, str(state.get("LastCommitSnapshotHash", ""))
    return current / "workspace-snapshot.json", str(state.get("PreparedSnapshotHash", ""))

def workspace_snapshot(repo: Path) -> dict[str, str]:
    try:
        return workspace_scope.workspace_snapshot(
            repo,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc


def workspace_file_paths(repo: Path) -> list[str]:
    try:
        return workspace_scope.workspace_paths(
            repo,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc


def workspace_path_in_scope(repo: Path, relative: str) -> bool:
    try:
        return workspace_scope.path_is_in_scope(
            repo,
            relative,
            fallback_ignored=ignored_workspace_path,
        )
    except workspace_scope.WorkspaceScopeError as exc:
        raise WorkflowStageError(str(exc)) from exc

def write_workspace_snapshot(repo: Path, path: Path) -> None:
    write_json(path, workspace_snapshot(repo))

def ignored_workspace_path(relative: str) -> bool:
    normalized = relative.replace("\\", "/").removeprefix("./")
    return normalized == "memory.md" or normalized.endswith("/memory.md") or any(
        normalized.startswith(prefix) or f"/{prefix}" in f"/{normalized}"
        for prefix in IGNORED_PREFIXES
    )

def repository_modified(repo: Path, current: Path, state: dict[str, object]) -> bool:
    try:
        return bool(workspace_changes(repo, current, state))
    except (OSError, WorkflowStageError):
        return False

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
from automation.semantic_verifier import (
    SemanticVerifierError,
    extract_acceptance_criteria,
    parse_semantic_output,
    prepare_semantic_repair_prompt,
    render_template,
)
from automation.workflow_commands import (
    _decoded_text,
    _run_captured,
)
from automation.workflow_contract import (
    DEFAULT_CI_CHECK_POLL_ATTEMPTS,
    DEFAULT_CI_CHECK_POLL_SECONDS,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
    FAILURE_TRANSIENT,
    WorkflowStageError,
    configured_attempt_limit,
    configured_nonnegative_float,
    issue_number_from_arguments,
)
from automation.workflow_diagnostics import (
    _record_shipment_diagnostics,
)
from automation.workflow_github import (
    create_api_commit,
    ensure_pr,
    wait_for_required_checks,
)
from automation.workflow_prompts import (
    render_ci_repair,
    render_legacy_verifier,
)
from automation.workflow_storage import (
    _file_sha256,
    read_state,
    read_text,
    write_json,
    write_state,
    write_text,
)
from automation.workflow_workspace import (
    source_identity,
    workspace_changes,
    write_workspace_snapshot,
)

def _preflight(repo: Path, arguments: str, which: Callable[[str], str | None]) -> None:
    if not repo.is_dir():
        raise WorkflowStageError(f"target repository is not a directory: {repo}")
    if not (repo / ".git").exists():
        raise WorkflowStageError(f"target repository is not a Git worktree: {repo}")
    missing = [tool for tool in ("git", "gh") if which(tool) is None]
    if missing:
        raise WorkflowStageError("required command is unavailable: " + ", ".join(missing))
    if not sys.executable:
        raise WorkflowStageError("Python executable is unavailable")
    if issue_number_from_arguments(arguments) == 0:
        raise WorkflowStageError("pass an issue number to /autodev-issue-to-pr")
    missing_config = [name for name in ("GITHUB_OWNER", "GITHUB_REPO") if not os.environ.get(name, "").strip()]
    if missing_config:
        raise WorkflowStageError("required AutoDev setting is unavailable: " + ", ".join(missing_config))
    configured_attempt_limit("MAX_REPAIR_ATTEMPTS", DEFAULT_MAX_REPAIR_ATTEMPTS)
    configured_attempt_limit("MAX_SEMANTIC_REPAIR_ATTEMPTS", DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS)
    configured_attempt_limit("CI_CHECK_POLL_ATTEMPTS", DEFAULT_CI_CHECK_POLL_ATTEMPTS)
    configured_nonnegative_float("CI_CHECK_POLL_SECONDS", DEFAULT_CI_CHECK_POLL_SECONDS)

def run_local_check(
    repo: Path,
    current: Path,
    state: dict[str, object],
    autodev_root: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    command = str(state.get("LocalCheck", "")).strip()
    if not command:
        raise WorkflowStageError("state.json has no LocalCheck command")
    completed = _run_captured(
        runner,
        command,
        cwd=repo,
        shell=True,
    )
    output = _decoded_text(getattr(completed, "stdout", "")) + _decoded_text(
        getattr(completed, "stderr", "")
    )
    write_text(current / "local-check.log", output)
    if int(getattr(completed, "returncode", 1)) == 0:
        state["Status"] = "LocalCheckPassed"
        state["LastLocalCheckPassed"] = True
        if state.get("VerificationProofVersion"):
            proof = source_identity(repo, current, state)
            state["VerifiedParentSha"] = proof["parent_sha"]
            state["VerifiedSourceIdentity"] = proof["identity"]
            state["VerifiedChanges"] = proof["changes"]
            state.pop("LastSemanticVerdict", None)
            state.pop("SemanticSourceIdentity", None)
            state.pop("CreatedCommitSha", None)
            state.pop("CreatedTreeSha", None)
            state.pop("CreatedParentSha", None)
            state.pop("ShippedSourceIdentity", None)
            state.pop("ShippedTreeVerified", None)
            state.pop("PrHeadSha", None)
            state.pop("CiProof", None)
            _record_shipment_diagnostics(
                current,
                verified_parent_sha=proof["parent_sha"],
                verified_source_identity=proof["identity"],
                verified_change_count=len(proof["changes"]),
            )
        write_state(current, state)
        return True

    template = read_text(autodev_root / "promptTemplates" / "local-repair.md")
    prompt = render_template(
        template,
        {
            "IssueText": read_text(current / "issue.md") or str(state.get("IssueText", "")),
            "FailureLog": output,
            "LocalCheck": command,
            "StackContext": str(state.get("StackContext", "")),
        },
    )
    write_text(current / "local-repair.md", prompt)
    state["Status"] = "LocalCheckFailed"
    state["LastLocalCheckPassed"] = False
    if state.get("VerificationProofVersion"):
        state.pop("VerifiedParentSha", None)
        state.pop("VerifiedSourceIdentity", None)
        state.pop("VerifiedChanges", None)
        state.pop("LastSemanticVerdict", None)
        state.pop("SemanticSourceIdentity", None)
        state.pop("CiProof", None)
    write_state(current, state)
    return False

def pr_and_ci(
    repo: Path,
    current: Path,
    state: dict[str, object],
    autodev_root: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    changes = workspace_changes(repo, current, state)
    if changes:
        write_json(current / "changed-files.json", changes)
        commit_sha = create_api_commit(repo, state, changes, current, runner=runner)
        state = read_state(current)
        state["LastCommitSha"] = commit_sha
        state["Status"] = "CommittedViaGitHubApi"
        state.pop("CommitTreeBaseSha", None)
        write_state(current, state)
        snapshot_path = current / "last-commit-workspace-snapshot.json"
        write_workspace_snapshot(repo, snapshot_path)
        state = read_state(current)
        state["LastCommitSnapshotHash"] = _file_sha256(snapshot_path)
        write_state(current, state)
    elif not str(state.get("PrUrl", "")).strip():
        raise WorkflowStageError("no workspace file changes detected, and no PR exists")

    state = read_state(current)
    ensure_pr(repo, current, state, runner=runner)
    state = read_state(current)
    ci_proof = wait_for_required_checks(repo, state, runner=runner)
    write_json(current / "ci-summary.json", ci_proof)
    state = read_state(current)
    state["CiProof"] = ci_proof
    if ci_proof["state"] == "terminal-failure":
        render_ci_repair(current, state, autodev_root)
        state["Status"] = "CiFailed"
        write_state(current, state)
        return False
    if ci_proof["state"] != "terminal-success":
        raise WorkflowStageError(
            f"required CI did not reach terminal success for {ci_proof.get('head_sha', '')}: {ci_proof.get('state', '')}",
            classification=FAILURE_TRANSIENT,
        )

    render_legacy_verifier(repo, current, state, autodev_root, runner=runner)
    state["Status"] = "CiPassedVerifierPromptRendered"
    write_state(current, state)
    return True

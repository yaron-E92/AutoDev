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
from automation import local_verification, repair_lineage, repository_identity
from automation.semantic_contract import SemanticVerifierError
from automation.semantic_invocation import prepare_semantic_repair_prompt
from automation.semantic_prompts import extract_acceptance_criteria
from automation.semantic_schema import parse_semantic_output
from automation.semantic_text import render_template
from automation.workflow_commands import (
    _decoded_text,
    _run_captured,
)
from automation.workflow_contract import (
    DEFAULT_CI_CHECK_POLL_ATTEMPTS,
    DEFAULT_CI_CHECK_POLL_SECONDS,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS,
    FAILURE_SETUP,
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

def _preflight(
    repo: Path,
    arguments: str,
    which: Callable[[str], str | None],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
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
    try:
        repository_identity.resolve_github_repository(repo, runner=runner)
    except repository_identity.RepositoryIdentityError as exc:
        raise WorkflowStageError(str(exc)) from exc
    configured_attempt_limit("MAX_REPAIR_ATTEMPTS", DEFAULT_MAX_REPAIR_ATTEMPTS)
    configured_attempt_limit("MAX_SEMANTIC_REPAIR_ATTEMPTS", DEFAULT_MAX_SEMANTIC_REPAIR_ATTEMPTS)
    configured_attempt_limit("CI_CHECK_POLL_ATTEMPTS", DEFAULT_CI_CHECK_POLL_ATTEMPTS)
    configured_nonnegative_float("CI_CHECK_POLL_SECONDS", DEFAULT_CI_CHECK_POLL_SECONDS)

def _clear_local_verification_proof(state: dict[str, object]) -> None:
    if state.get("VerificationProofVersion"):
        state.pop("VerifiedParentSha", None)
        state.pop("VerifiedSourceIdentity", None)
        state.pop("VerifiedChanges", None)
        state.pop("LastSemanticVerdict", None)
        state.pop("SemanticSourceIdentity", None)
        state.pop("CiProof", None)

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
        raise WorkflowStageError(
            "state.json has no LocalCheck command",
            classification=FAILURE_SETUP,
        )

    try:
        refreshed, source, profiles_path = local_verification.refreshed_local_check(
            state,
            autodev_root,
            which=shutil.which,
        )
        if refreshed != command or source != str(state.get("LocalCheckSource", "")):
            command = refreshed
            state["LocalCheck"] = command
            state["LocalCheckSource"] = source
            state["ProfilesPath"] = str(profiles_path)
            write_state(current, state)

        # Newly prepared runs always have provenance. Legacy runs are checked
        # when they contain the old shipped PowerShell verifier so upgrading
        # AutoDev can recover them without consuming a Fixer attempt.
        if source in {"explicit", "profile"} or local_verification.is_legacy_autodev_default(command):
            local_verification.preflight_local_check(
                command,
                explicit=source == "explicit",
                profiles_path=profiles_path,
                autodev_root=autodev_root,
                cwd=repo,
                which=shutil.which,
            )

        if local_verification.is_builtin_local_check(command):
            result = local_verification.run_recommended_verification(
                repo,
                current,
                runner=runner,
                which=shutil.which,
            )
            returncode = result.returncode
            output = result.output
        else:
            completed = _run_captured(
                runner,
                command,
                cwd=repo,
                shell=True,
            )
            returncode = int(getattr(completed, "returncode", 1))
            output = _decoded_text(getattr(completed, "stdout", "")) + _decoded_text(
                getattr(completed, "stderr", "")
            )
    except WorkflowStageError as exc:
        if exc.classification != FAILURE_SETUP:
            raise
        output = f"local verification setup/configuration failure: {exc}\n"
        write_text(current / "local-check.log", output)
        state["Status"] = "LocalCheckSetupFailed"
        state["LastLocalCheckPassed"] = False
        state["LocalCheckFailureClassification"] = FAILURE_SETUP
        state["LocalCheckFailureReason"] = str(exc)
        repair_lineage.clear_current_local_failure(state)
        _clear_local_verification_proof(state)
        write_state(current, state)
        raise

    write_text(current / "local-check.log", output)
    state.pop("LocalCheckFailureClassification", None)
    state.pop("LocalCheckFailureReason", None)
    if returncode == 0:
        state["Status"] = "LocalCheckPassed"
        state["LastLocalCheckPassed"] = True
        repair_lineage.clear_current_local_failure(state)
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
    fingerprint = repair_lineage.local_failure_fingerprint(command, output, returncode)
    repair_lineage.register_local_failure(state, fingerprint)
    _clear_local_verification_proof(state)
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

    state["Status"] = "CiPassed"
    write_state(current, state)
    return True
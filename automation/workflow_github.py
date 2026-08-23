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
    _command_failure_classification,
    _command_reason,
    _decoded_text,
    gh,
    gh_json,
)
from automation.workflow_contract import (
    CURRENT_DIR,
    DEFAULT_CI_CHECK_POLL_ATTEMPTS,
    DEFAULT_CI_CHECK_POLL_SECONDS,
    FAILURE_TRANSIENT,
    WorkflowStageError,
    concise,
    configured_attempt_limit,
    configured_nonnegative_float,
)
from automation.workflow_diagnostics import (
    _record_shipment_diagnostics,
)
from automation.workflow_prompts import (
    commit_message,
)
from automation.workflow_storage import (
    _json_evidence,
    read_state,
    read_text,
    write_json,
    write_state,
    write_text,
)
from automation.workflow_workspace import (
    source_identity,
)

def create_api_commit(
    repo: Path,
    state: dict[str, object],
    changes: list[dict[str, str]],
    current: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> str:
    repo_full = str(state.get("RepoFullName", ""))
    branch = str(state.get("BranchName", ""))
    base_sha = str(state.get("BaseSha", "")).strip()
    parent = str(state.get("LastCommitSha", "")).strip() or base_sha
    if not repo_full or not branch or not parent:
        raise WorkflowStageError("state.json is missing repository/branch/base commit information")

    verified_proof: dict[str, object] | None = None
    if state.get("VerificationProofVersion"):
        if not bool(state.get("LastLocalCheckPassed")):
            raise WorkflowStageError("API commit refused because local verification is not current")
        verified_proof = source_identity(repo, current, state)
        expected_identity = str(state.get("VerifiedSourceIdentity", ""))
        expected_parent = str(state.get("VerifiedParentSha", ""))
        if not expected_identity or verified_proof["identity"] != expected_identity:
            raise WorkflowStageError(
                "API commit refused because the workspace no longer matches the source state that passed local verification"
            )
        if not expected_parent or verified_proof["parent_sha"] != expected_parent or parent != expected_parent:
            raise WorkflowStageError(
                "API commit refused because the commit parent no longer matches the parent used for local verification"
            )
        supplied = sorted(
            ({"path": str(item["Path"]), "status": str(item["Status"])} for item in changes),
            key=lambda item: item["path"],
        )
        verified_paths = [
            {"path": str(item["path"]), "status": str(item["status"])}
            for item in verified_proof["changes"]
            if isinstance(item, dict)
        ]
        if supplied != verified_paths:
            raise WorkflowStageError(
                "API commit refused because the changed-file set differs from the locally verified source identity"
            )

    base_tree = ""
    if parent == base_sha:
        base_tree = str(state.get("BaseTreeSha", "")).strip()
    if not base_tree:
        parent_commit = gh_json(repo, ["api", f"repos/{repo_full}/git/commits/{parent}"], runner=runner)
        tree = parent_commit.get("tree", {})
        base_tree = str(tree.get("sha", "")) if isinstance(tree, dict) else ""
        if not base_tree:
            raise WorkflowStageError(
                f"could not resolve base tree for API commit parent {parent}; GitHub response: {_json_evidence(parent_commit)}"
            )
        if parent == base_sha:
            state["BaseTreeSha"] = base_tree
            write_state(current, state)
    if not base_tree:
        raise WorkflowStageError(f"could not resolve base tree for API commit parent {parent}")

    verified_by_path: dict[str, dict[str, str]] = {}
    if verified_proof is not None:
        verified_by_path = {
            str(item["path"]): item
            for item in verified_proof["changes"]
            if isinstance(item, dict)
        }

    tree_items: list[dict[str, object]] = []
    for change in changes:
        relative = str(change["Path"])
        if change["Status"] == "deleted":
            tree_items.append({"path": relative, "mode": "100644", "type": "blob", "sha": None})
            continue
        path = repo / relative
        if not path.is_file():
            raise WorkflowStageError(f"changed file does not exist: {path}")
        content = path.read_bytes()
        if verified_by_path:
            actual_digest = hashlib.sha256(content).hexdigest().upper()
            expected_digest = str(verified_by_path.get(relative, {}).get("sha256", ""))
            if not expected_digest or actual_digest != expected_digest:
                raise WorkflowStageError(
                    f"API commit refused because {relative} changed after local verification"
                )
        blob = gh_json(
            repo,
            ["api", f"repos/{repo_full}/git/blobs", "--method", "POST", "--input", "-"],
            input_text=json.dumps(
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                }
            ),
            runner=runner,
        )
        blob_sha = str(blob.get("sha", ""))
        if not blob_sha:
            raise WorkflowStageError(
                f"GitHub API did not return a blob SHA for {relative}; response: {_json_evidence(blob)}"
            )
        tree_items.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob_sha})

    tree = gh_json(
        repo,
        ["api", f"repos/{repo_full}/git/trees", "--method", "POST", "--input", "-"],
        input_text=json.dumps({"base_tree": base_tree, "tree": tree_items}),
        runner=runner,
    )
    tree_sha = str(tree.get("sha", ""))
    if not tree_sha:
        raise WorkflowStageError(
            f"GitHub API did not return a tree SHA; response: {_json_evidence(tree)}"
        )
    message = commit_message(current, state)
    commit = gh_json(
        repo,
        ["api", f"repos/{repo_full}/git/commits", "--method", "POST", "--input", "-"],
        input_text=json.dumps({"message": message, "tree": tree_sha, "parents": [parent]}),
        runner=runner,
    )
    sha = str(commit.get("sha", ""))
    if not sha:
        raise WorkflowStageError(
            f"GitHub API did not return a commit SHA; response: {_json_evidence(commit)}"
        )

    if state.get("VerificationProofVersion"):
        created = gh_json(repo, ["api", f"repos/{repo_full}/git/commits/{sha}"], runner=runner)
        created_tree = created.get("tree", {})
        created_tree_sha = str(created_tree.get("sha", "")) if isinstance(created_tree, dict) else ""
        created_parents = created.get("parents", [])
        created_parent = ""
        if isinstance(created_parents, list) and created_parents and isinstance(created_parents[0], dict):
            created_parent = str(created_parents[0].get("sha", ""))
        if created_tree_sha != tree_sha or created_parent != parent:
            raise WorkflowStageError(
                "GitHub commit proof did not match the locally verified shipment: "
                f"expected tree={tree_sha} parent={parent}; got tree={created_tree_sha or '<missing>'} "
                f"parent={created_parent or '<missing>'}; response={_json_evidence(created)}"
            )
        state["CreatedCommitSha"] = sha
        state["CreatedTreeSha"] = tree_sha
        state["CreatedParentSha"] = parent
        state["ShippedSourceIdentity"] = str(verified_proof["identity"] if verified_proof else "")
        state["ShippedTreeVerified"] = True
        write_state(current, state)
        _record_shipment_diagnostics(
            current,
            created_commit_sha=sha,
            created_tree_sha=tree_sha,
            created_parent_sha=parent,
            shipped_source_identity=state["ShippedSourceIdentity"],
        )

    ref_path = f"heads/{branch}"
    existing = gh(repo, ["api", f"repos/{repo_full}/git/ref/{ref_path}"], runner=runner, check=False)
    if int(getattr(existing, "returncode", 1)) == 0:
        gh(
            repo,
            ["api", f"repos/{repo_full}/git/refs/{ref_path}", "--method", "PATCH", "--input", "-"],
            input_text=json.dumps({"sha": sha, "force": False}),
            runner=runner,
        )
    else:
        gh(
            repo,
            ["api", f"repos/{repo_full}/git/refs", "--method", "POST", "--input", "-"],
            input_text=json.dumps({"ref": f"refs/heads/{branch}", "sha": sha}),
            runner=runner,
        )
    return sha

def ensure_pr(
    repo: Path,
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    repo_full = str(state.get("RepoFullName", ""))
    pr_url = str(state.get("PrUrl", "")).strip()
    if not pr_url:
        body = (
            "Implements:\n\n"
            + (read_text(current / "issue.md") or str(state.get("IssueText", "")))
            + "\nAutoDev plan:\n\n"
            + read_text(current / "plan.md")
            + "\nLocal verification:\n\n```text\n"
            + str(state.get("LocalCheck", ""))
            + "\n```\n"
        )
        body_path = current / "pr-body.md"
        write_text(body_path, body)
        completed = gh(
            repo,
            [
                "pr",
                "create",
                "--repo",
                repo_full,
                "--base",
                str(state.get("Base", "main")),
                "--head",
                str(state.get("BranchName", "")),
                "--title",
                str(state.get("IssueTitle", "AutoDev change")),
                "--body-file",
                str(body_path),
            ],
            runner=runner,
        )
        lines = [line.strip() for line in _decoded_text(getattr(completed, "stdout", "")).splitlines() if line.strip()]
        pr_url = lines[-1] if lines else ""
        if not pr_url:
            raise WorkflowStageError(
                f"gh pr create did not return a PR URL: {_command_reason(completed)}"
            )
        state["PrUrl"] = pr_url

    selector = str(state.get("PrNumber", 0) or 0) or pr_url
    details = gh_json(
        repo,
        ["pr", "view", selector, "--repo", repo_full, "--json", "number,headRefOid"],
        runner=runner,
    )
    state["PrNumber"] = int(details.get("number", 0) or 0)
    head_sha = str(details.get("headRefOid", "")).strip()
    if state.get("VerificationProofVersion"):
        expected = str(state.get("LastCommitSha", "")).strip()
        if not expected or not head_sha:
            raise WorkflowStageError("PR head proof is missing the expected/current head SHA")
        if head_sha != expected:
            raise WorkflowStageError(
                f"PR head {head_sha} does not match the exact AutoDev commit {expected}"
            )
    state["PrHeadSha"] = head_sha
    write_state(current, state)
    _record_shipment_diagnostics(current, pr_head_sha=head_sha)

def wait_for_required_checks(
    repo: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    repo_full = str(state.get("RepoFullName", ""))
    pr_number = int(state.get("PrNumber", 0) or 0)
    expected_head = str(state.get("LastCommitSha", "")).strip()
    if pr_number <= 0:
        raise WorkflowStageError("state.json has no PR number")
    if not expected_head:
        raise WorkflowStageError("state.json has no commit SHA to bind CI checks to")

    attempts = configured_attempt_limit("CI_CHECK_POLL_ATTEMPTS", DEFAULT_CI_CHECK_POLL_ATTEMPTS)
    if attempts <= 0:
        attempts = 1
    interval = configured_nonnegative_float("CI_CHECK_POLL_SECONDS", DEFAULT_CI_CHECK_POLL_SECONDS)
    current = repo / CURRENT_DIR
    last_proof: dict[str, object] = {
        "head_sha": expected_head,
        "state": "not-observed",
        "checks": [],
        "polls": 0,
        "required_only": True,
    }

    for poll in range(1, attempts + 1):
        head_before = _pr_head_sha(repo, repo_full, pr_number, runner=runner)
        if head_before != expected_head:
            raise WorkflowStageError(
                f"required CI is attached to PR head {head_before or '<missing>'}, not expected AutoDev commit {expected_head}"
            )

        checks = _query_pr_checks(repo, repo_full, pr_number, required=True, runner=runner)
        required_only = True
        if not checks:
            checks = _query_pr_checks(repo, repo_full, pr_number, required=False, runner=runner)
            required_only = False

        head_after = _pr_head_sha(repo, repo_full, pr_number, runner=runner)
        if head_after != expected_head:
            raise WorkflowStageError(
                f"PR head changed while CI was being observed: expected {expected_head}, got {head_after or '<missing>'}"
            )

        state_name = _ci_state(checks)
        last_proof = {
            "head_sha": expected_head,
            "state": state_name,
            "checks": checks,
            "polls": poll,
            "required_only": required_only,
        }
        _persist_ci_proof(current, last_proof)
        if state_name in {"terminal-success", "terminal-failure"}:
            return last_proof
        if poll < attempts and interval:
            sleep(interval)

    raise WorkflowStageError(
        f"required CI for exact PR head {expected_head} did not reach terminal state after {attempts} polls; last state={last_proof['state']}",
        classification=FAILURE_TRANSIENT,
    )

def _query_pr_checks(
    repo: Path,
    repo_full: str,
    pr_number: int,
    *,
    required: bool,
    runner: Callable[..., object],
) -> list[dict[str, object]]:
    arguments = [
        "pr",
        "checks",
        str(pr_number),
        "--repo",
        repo_full,
    ]
    if required:
        arguments.append("--required")
    arguments.extend(["--json", "name,bucket,state,description,link"])
    completed = gh(repo, arguments, runner=runner, check=False)
    text = _decoded_text(getattr(completed, "stdout", "")).strip()
    stderr = _decoded_text(getattr(completed, "stderr", "")).strip()
    if "\ufffd" in text:
        raise WorkflowStageError("gh pr checks returned invalid UTF-8 JSON output")
    if not text:
        if int(getattr(completed, "returncode", 1)) != 0:
            lowered = stderr.casefold()
            if "no checks" not in lowered and "no required" not in lowered:
                raise WorkflowStageError(
                    _command_reason(completed),
                    classification=_command_failure_classification(completed),
                )
        return []
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowStageError(
            f"gh pr checks returned invalid JSON: {concise(text, 700)}"
        ) from exc
    if not isinstance(value, list):
        raise WorkflowStageError(
            f"gh pr checks returned an unexpected JSON value: {concise(text, 700)}"
        )
    return [item for item in value if isinstance(item, dict)]

def _ci_state(checks: list[dict[str, object]]) -> str:
    if not checks:
        return "not-observed"
    buckets = [str(item.get("bucket", "")).strip().casefold() for item in checks]
    if any(bucket == "pending" for bucket in buckets):
        return "queued/in-progress"
    if all(bucket == "pass" for bucket in buckets):
        return "terminal-success"
    return "terminal-failure"

def _persist_ci_proof(current: Path, proof: dict[str, object]) -> None:
    write_json(current / "ci-summary.json", proof)
    state = read_state(current)
    state["CiProof"] = proof
    write_state(current, state)
    _record_shipment_diagnostics(
        current,
        ci_state=proof.get("state", ""),
        ci_head_sha=proof.get("head_sha", ""),
        ci_polls=proof.get("polls", 0),
        ci_checks=[
            {
                "name": str(item.get("name", "")),
                "bucket": str(item.get("bucket", "")),
                "state": str(item.get("state", "")),
            }
            for item in proof.get("checks", [])
            if isinstance(item, dict)
        ],
    )

def _pr_head_sha(
    repo: Path,
    repo_full: str,
    pr_number: int,
    *,
    runner: Callable[..., object],
) -> str:
    details = gh_json(
        repo,
        ["pr", "view", str(pr_number), "--repo", repo_full, "--json", "number,headRefOid"],
        runner=runner,
    )
    return str(details.get("headRefOid", "")).strip()

def validate_ready_proof(
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    if not state.get("VerificationProofVersion"):
        return
    repo = current.parents[1]
    repo_full = str(state.get("RepoFullName", ""))
    commit_sha = str(state.get("LastCommitSha", "")).strip()
    created_sha = str(state.get("CreatedCommitSha", "")).strip()
    created_tree = str(state.get("CreatedTreeSha", "")).strip()
    created_parent = str(state.get("CreatedParentSha", "")).strip()
    verified_parent = str(state.get("VerifiedParentSha", "")).strip()
    verified_identity = str(state.get("VerifiedSourceIdentity", "")).strip()
    shipped_identity = str(state.get("ShippedSourceIdentity", "")).strip()
    if not commit_sha or created_sha != commit_sha:
        raise WorkflowStageError("ready refused: durable state does not prove the current AutoDev commit")
    if not created_tree or not created_parent or not bool(state.get("ShippedTreeVerified")):
        raise WorkflowStageError("ready refused: shipped commit/tree proof is missing")
    if not verified_identity or shipped_identity != verified_identity or verified_parent != created_parent:
        raise WorkflowStageError("ready refused: shipped tree is not bound to the source identity that passed local verification")
    if not bool(state.get("LastLocalCheckPassed")):
        raise WorkflowStageError("ready refused: deterministic local verification is not current")

    semantic_required = bool(state.get("OpenCodeProtocolVersion")) or "LastSemanticVerdict" in state
    if semantic_required:
        if str(state.get("LastSemanticVerdict", "")) != "pass":
            raise WorkflowStageError("ready refused: semantic verification has not passed")
        if str(state.get("SemanticSourceIdentity", "")) != shipped_identity:
            raise WorkflowStageError("ready refused: semantic verification does not apply to the shipped source identity")

    ci_proof = state.get("CiProof", {})
    if not isinstance(ci_proof, dict):
        raise WorkflowStageError("ready refused: CI proof is missing")
    checks = ci_proof.get("checks", [])
    if (
        ci_proof.get("state") != "terminal-success"
        or str(ci_proof.get("head_sha", "")) != commit_sha
        or not isinstance(checks, list)
        or not checks
        or any(str(item.get("bucket", "")).casefold() != "pass" for item in checks if isinstance(item, dict))
    ):
        raise WorkflowStageError("ready refused: required CI is not terminal-success for the exact current PR head")

    pr_number = int(state.get("PrNumber", 0) or 0)
    if pr_number <= 0 or not str(state.get("PrUrl", "")).strip():
        raise WorkflowStageError("ready refused: PR proof is missing")
    current_head = _pr_head_sha(repo, repo_full, pr_number, runner=runner)
    if current_head != commit_sha:
        raise WorkflowStageError(
            f"ready refused: current PR head {current_head or '<missing>'} differs from verified AutoDev commit {commit_sha}"
        )
    commit = gh_json(repo, ["api", f"repos/{repo_full}/git/commits/{commit_sha}"], runner=runner)
    tree = commit.get("tree", {})
    actual_tree = str(tree.get("sha", "")) if isinstance(tree, dict) else ""
    parents = commit.get("parents", [])
    actual_parent = ""
    if isinstance(parents, list) and parents and isinstance(parents[0], dict):
        actual_parent = str(parents[0].get("sha", ""))
    if actual_tree != created_tree or actual_parent != created_parent:
        raise WorkflowStageError(
            "ready refused: GitHub commit tree/parent no longer matches the durable shipped-tree proof"
        )

def mark_ready(
    current: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    issue_number = int(state.get("IssueNumber", 0) or 0)
    repo_full = str(state.get("RepoFullName", ""))
    repo = current.parents[1]
    if issue_number:
        gh(
            repo,
            [
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                repo_full,
                "--remove-label",
                "autodev:running",
                "--remove-label",
                "autodev:blocked",
                "--add-label",
                "autodev:done",
            ],
            runner=runner,
        )
        gh(
            repo,
            [
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repo_full,
                "--body",
                f"AutoDev automation completed.\n\nPR:\n{state.get('PrUrl', '')}\n\nStatus:\nReady for review/merge.",
            ],
            runner=runner,
        )
    state["Status"] = "ReadyForReview"
    write_state(current, state)

def mark_blocked(
    current: Path,
    state: dict[str, object],
    reason: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    issue_number = int(state.get("IssueNumber", 0) or 0)
    repo_full = str(state.get("RepoFullName", ""))
    repo = current.parents[1]
    if issue_number:
        gh(
            repo,
            [
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                repo_full,
                "--remove-label",
                "autodev:running",
                "--add-label",
                "autodev:blocked",
            ],
            runner=runner,
        )
        gh(
            repo,
            [
                "issue",
                "comment",
                str(issue_number),
                "--repo",
                repo_full,
                "--body",
                f"AutoDev automation blocked.\n\nReason:\n\n```text\n{reason}\n```",
            ],
            runner=runner,
        )
    state["Status"] = "Blocked"
    write_state(current, state)

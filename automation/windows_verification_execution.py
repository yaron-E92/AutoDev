from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from automation.windows_verification_actions import (
    _current_autodev_ref,
    _failed_logs,
    _list_workflow_runs,
    validate_actions_installation,
)
from automation.windows_verification_config import (
    load_config,
)
from automation.windows_verification_contract import (
    CONFIG_PATH,
    DEFAULT_CALLER_WORKFLOW,
    DEFAULT_POLL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    FAILURE_CODE_REPAIRABLE,
    FAILURE_DETERMINISTIC,
    MANIFEST_STAGE,
    MAX_CAPTURE_CHARS,
    REPAIR_FILE,
    REQUEST_FILE,
    RESULT_FILE,
    SCHEMA_VERSION,
    WindowsVerificationError,
    _COMMAND_MARKER,
    utc_now,
)
from automation.windows_verification_failure import (
    _blocked_failure,
    _infrastructure_failure,
    _looks_transient_text,
    _render_repair,
)
from automation.windows_verification_manifest import (
    current_repair_attempt,
    proof_current,
    sync_manifest,
    windows_required,
)
from automation.windows_verification_process import (
    _returncode,
    _run,
    _stderr,
    _stdout,
)
from automation.windows_verification_storage import (
    _read_json,
    _sha256_file,
    _write_json,
)

def run_after_push(
    repo: Path,
    current: Path,
    state: dict[str, object],
    *,
    max_repair_attempts: int,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object] | None:
    if not windows_required(state):
        return None
    if proof_current(state):
        return {
            "state": "CONTINUE",
            "platform_verification_stage": MANIFEST_STAGE,
            "windows_repair_attempt": current_repair_attempt(repo),
            "windows_verification_proof": state.get("WindowsVerificationProof", {}),
            "windows_stage_completed": True,
        }

    attempt = current_repair_attempt(repo)
    config = load_config(repo)
    if not config or not bool(config.get("enabled", True)):
        reason = (
            "deferred Windows verification is required, but "
            f"{CONFIG_PATH.as_posix()} is not configured and enabled"
        )
        return _blocked_failure(repo, current, state, attempt, reason)

    repo_full = str(state.get("RepoFullName", "")).strip()
    try:
        installation = validate_actions_installation(
            repo,
            repo_full=repo_full,
            config=config,
            runner=runner,
        )
    except WindowsVerificationError as exc:
        return _blocked_failure(repo, current, state, attempt, str(exc))

    head = str(state.get("LastCommitSha", "")).strip()
    source = str(state.get("ShippedSourceIdentity", "")).strip()
    branch = str(state.get("BranchName", "")).strip()
    if not head or not source or not branch or not bool(state.get("ShippedTreeVerified")):
        raise WindowsVerificationError(
            "Windows verification refused because the pushed commit/source-identity proof is incomplete"
        )
    existing_pr_head = str(state.get("PrHeadSha", "")).strip()
    if existing_pr_head and existing_pr_head != head:
        raise WindowsVerificationError(
            f"Windows verification refused because PR head {existing_pr_head} differs from pushed commit {head}"
        )
    try:
        autodev_ref = _current_autodev_ref(runner)
    except WindowsVerificationError as exc:
        return _blocked_failure(repo, current, state, attempt, str(exc))

    workflow = str(config.get("workflow", DEFAULT_CALLER_WORKFLOW))
    request = {
        "version": SCHEMA_VERSION,
        "transport": "github-actions",
        "repo_full_name": repo_full,
        "workflow": workflow,
        "branch": branch,
        "commit_sha": head,
        "source_identity": source,
        "autodev_ref": autodev_ref,
        "obligations": [
            item
            for item in state.get("DeferredVerificationObligations", [])
            if isinstance(item, dict) and item.get("platform") == "windows"
        ],
        "commands": list(config.get("commands", [])),
    }
    request_path = current / REQUEST_FILE
    _write_json(request_path, request)

    try:
        before = _list_workflow_runs(repo, repo_full, workflow, branch, runner)
    except (OSError, subprocess.TimeoutExpired, WindowsVerificationError) as exc:
        return _infrastructure_failure(repo, current, state, attempt, str(exc))
    previous_ids = {int(item.get("databaseId", 0) or 0) for item in before}

    commands_json = json.dumps(request["commands"], separators=(",", ":"), ensure_ascii=False)
    try:
        dispatched = _run(
            runner,
            [
                "gh",
                "workflow",
                "run",
                workflow,
                "--repo",
                repo_full,
                "--ref",
                branch,
                "-f",
                f"expected_sha={head}",
                "-f",
                f"source_identity={source}",
                "-f",
                f"commands_json={commands_json}",
                "-f",
                f"autodev_ref={autodev_ref}",
            ],
            cwd=repo,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            f"could not dispatch Windows GitHub Actions workflow: {exc}",
        )
    if _returncode(dispatched) != 0:
        detail = (_stderr(dispatched) or _stdout(dispatched) or "no output")[-2000:]
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            f"Windows GitHub Actions dispatch failed: {detail}",
        )

    timeout_seconds = int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
    poll_seconds = max(0.0, float(os.environ.get("AUTODEV_WINDOWS_ACTIONS_POLL_SECONDS", DEFAULT_POLL_SECONDS)))
    deadline = time.monotonic() + timeout_seconds
    run: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            candidates = _list_workflow_runs(repo, repo_full, workflow, branch, runner)
        except (OSError, subprocess.TimeoutExpired, WindowsVerificationError) as exc:
            return _infrastructure_failure(repo, current, state, attempt, str(exc))
        fresh = [
            item
            for item in candidates
            if int(item.get("databaseId", 0) or 0) not in previous_ids
            and str(item.get("headSha", "")) == head
        ]
        if fresh:
            run = fresh[0]
            if str(run.get("status", "")).casefold() == "completed":
                break
        if poll_seconds:
            time.sleep(poll_seconds)
    if run is None or str(run.get("status", "")).casefold() != "completed":
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            f"Windows GitHub Actions workflow did not complete within {timeout_seconds} seconds",
        )

    run_id = int(run.get("databaseId", 0) or 0)
    run_url = str(run.get("url", ""))
    conclusion = str(run.get("conclusion", "")).casefold()
    logs = ""
    result_state = "passed"
    reason = ""
    commands_result = [
        {"name": str(item.get("name", "")), "returncode": 0, "output": run_url}
        for item in request["commands"]
        if isinstance(item, dict)
    ]
    if conclusion != "success":
        logs = _failed_logs(repo, repo_full, run_id, runner)
        if conclusion in {"cancelled", "timed_out", "action_required", "startup_failure", "stale"}:
            result_state = "infrastructure-failure"
            reason = f"GitHub Actions Windows run concluded {conclusion}"
        elif _COMMAND_MARKER not in logs:
            result_state = "infrastructure-failure"
            reason = "GitHub Actions Windows run failed before an AutoDev verification command started"
        elif _looks_transient_text(logs):
            result_state = "infrastructure-failure"
            reason = "Windows verification command failed with transient infrastructure evidence"
        else:
            result_state = "code-failure"
            reason = "Windows verification command failed"
        commands_result = [
            {
                "name": "github-actions-windows",
                "returncode": 1,
                "output": logs[-MAX_CAPTURE_CHARS:],
            }
        ]

    normalized = {
        "version": SCHEMA_VERSION,
        "state": result_state,
        "platform": "windows",
        "transport": "github-actions",
        "workflow": workflow,
        "commit_sha": head,
        "source_identity": source,
        "autodev_ref": autodev_ref,
        "run_id": run_id,
        "run_url": run_url,
        "conclusion": conclusion,
        "reason": reason,
        "commands": commands_result,
        "installation": installation,
    }
    result_path = current / RESULT_FILE
    _write_json(result_path, normalized)

    if result_state == "passed":
        proof = {
            "state": "terminal-success",
            "platform": "windows",
            "transport": "github-actions",
            "workflow": workflow,
            "run_id": run_id,
            "run_url": run_url,
            "head_sha": head,
            "source_identity": source,
            "autodev_ref": autodev_ref,
            "result_sha256": _sha256_file(result_path),
            "command_names": [
                str(item.get("name", ""))
                for item in request["commands"]
                if isinstance(item, dict) and str(item.get("name", ""))
            ],
            "obligation_ids": [
                str(item.get("id", ""))
                for item in request["obligations"]
                if isinstance(item, dict) and str(item.get("id", ""))
            ],
            "completed_at": utc_now(),
        }
        state["WindowsVerificationProof"] = proof
        state.pop("LastWindowsVerificationFailure", None)
        state["Status"] = "WindowsVerificationPassed"
        _write_json(current / "state.json", state)
        sync_manifest(repo, state)
        return {
            "state": "CONTINUE",
            "failed_stage": "",
            "reason": "",
            "failure_classification": "",
            "next_action": "continue PR/CI shipment proof",
            "artifact": str(result_path),
            "platform_verification_stage": MANIFEST_STAGE,
            "windows_verification_proof": proof,
            "windows_repair_attempt": attempt,
            "windows_stage_completed": True,
        }

    if result_state == "code-failure":
        repair_path = current / REPAIR_FILE
        _render_repair(current, state, normalized, repair_path)
        state.pop("WindowsVerificationProof", None)
        state["Status"] = "WindowsVerificationFailed"
        state["LastWindowsVerificationFailure"] = {
            "classification": FAILURE_CODE_REPAIRABLE,
            "attempt": attempt,
            "head_sha": head,
            "source_identity": source,
            "run_id": run_id,
            "run_url": run_url,
            "artifact": f".autodev-run/current/{REPAIR_FILE}",
        }
        _write_json(current / "state.json", state)
        sync_manifest(repo, state)
        if attempt >= max_repair_attempts:
            return {
                "state": "BLOCKED",
                "failed_stage": "windows-verification",
                "reason": "Windows verification repair-attempt limit exhausted",
                "failure_classification": FAILURE_DETERMINISTIC,
                "next_action": "mark the run blocked",
                "artifact": str(repair_path),
                "platform_verification_stage": MANIFEST_STAGE,
                "windows_repair_attempt": attempt,
            }
        return {
            "state": "REPAIR",
            "failed_stage": "windows-verification",
            "reason": "Windows-only verification failed on the pushed AutoDev commit",
            "failure_classification": FAILURE_CODE_REPAIRABLE,
            "next_action": "delegate the Windows repair to autodev-fixer, then rerun local, semantic, push, Windows verification, PR and CI",
            "artifact": str(repair_path),
            "platform_verification_stage": MANIFEST_STAGE,
            "windows_repair_attempt": attempt,
        }

    return _infrastructure_failure(
        repo,
        current,
        state,
        attempt,
        reason or f"Windows GitHub Actions run concluded {conclusion or 'failure'}",
        preserve_result=True,
        run_id=run_id,
        run_url=run_url,
    )

def run_after_ci(
    repo: Path,
    current: Path,
    state: dict[str, object],
    *,
    max_repair_attempts: int,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object] | None:
    return run_after_push(
        repo,
        current,
        state,
        max_repair_attempts=max_repair_attempts,
        runner=runner,
    )

def validate_ready(current: Path, state: dict[str, object]) -> None:
    if not windows_required(state):
        return
    if not proof_current(state):
        raise WindowsVerificationError(
            "ready refused: deferred Windows verification is required but no current GitHub Actions terminal-success proof exists"
        )
    proof = state.get("WindowsVerificationProof")
    assert isinstance(proof, dict)
    result_path = current / RESULT_FILE
    expected_hash = str(proof.get("result_sha256", ""))
    if not result_path.is_file() or not expected_hash or _sha256_file(result_path) != expected_hash:
        raise WindowsVerificationError(
            "ready refused: Windows verification result artifact is missing or changed"
        )
    result = _read_json(result_path)
    if not isinstance(result, dict):
        raise WindowsVerificationError("ready refused: Windows verification result is invalid")
    if (
        result.get("state") != "passed"
        or str(result.get("platform", "")).casefold() != "windows"
        or str(result.get("transport", "")) != "github-actions"
        or str(result.get("commit_sha", "")) != str(state.get("PrHeadSha", ""))
        or str(result.get("source_identity", "")) != str(state.get("ShippedSourceIdentity", ""))
        or not int(result.get("run_id", 0) or 0)
    ):
        raise WindowsVerificationError(
            "ready refused: Windows GitHub Actions result is not bound to the current shipped source"
        )

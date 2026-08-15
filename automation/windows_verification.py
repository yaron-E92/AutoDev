from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


AUTODEV_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(".autodev") / "windows-verification.json"
DEFAULT_CALLER_WORKFLOW = "autodev-windows-verification.yml"
REQUEST_FILE = "windows-verification-request.json"
RESULT_FILE = "windows-verification-result.json"
REPAIR_FILE = "windows-repair.md"
MANIFEST_STAGE = "windows-verified"
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 3600
DEFAULT_POLL_SECONDS = 5.0
MAX_CAPTURE_CHARS = 24000

FAILURE_CODE_REPAIRABLE = "code-repairable"
FAILURE_TRANSIENT = "transient/retryable-infrastructure"
FAILURE_DETERMINISTIC = "non-retryable-deterministic"

_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "could not resolve host",
    "name resolution",
    "network is unreachable",
    "rate limit",
    "http 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "service unavailable",
    "unable to load the service index",
    "the hosted runner",
    "runner has received a shutdown signal",
    "failed to download action",
    "unable to resolve action",
    "the operation was canceled",
)
_COMMAND_MARKER = "AUTODEV_WINDOWS_COMMAND_START="
_ACTIONS_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WindowsVerificationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return ""


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _run(
    runner: Callable[..., object],
    command: list[str],
    *,
    cwd: Path,
    timeout: int | None = None,
) -> object:
    return runner(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _stdout(completed: object) -> str:
    return str(getattr(completed, "stdout", "") or "").strip()


def _stderr(completed: object) -> str:
    return str(getattr(completed, "stderr", "") or "").strip()


def _returncode(completed: object) -> int:
    return int(getattr(completed, "returncode", 1))


def _json_stdout(completed: object, context: str) -> object:
    if _returncode(completed) != 0:
        raise WindowsVerificationError(
            f"{context} failed: {(_stderr(completed) or _stdout(completed) or 'no output')[-2000:]}"
        )
    try:
        return json.loads(_stdout(completed) or "null")
    except json.JSONDecodeError as exc:
        raise WindowsVerificationError(f"{context} returned invalid JSON") from exc


def _current_autodev_ref(runner: Callable[..., object]) -> str:
    try:
        completed = _run(
            runner,
            ["git", "rev-parse", "HEAD"],
            cwd=AUTODEV_ROOT,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowsVerificationError(f"could not resolve the current AutoDev revision: {exc}") from exc
    if _returncode(completed) != 0:
        detail = (_stderr(completed) or _stdout(completed) or "git rev-parse failed")[-1200:]
        raise WindowsVerificationError(f"could not resolve the current AutoDev revision: {detail}")
    value = _stdout(completed)
    if len(value) != 40 or any(character not in "0123456789abcdefABCDEF" for character in value):
        raise WindowsVerificationError(
            f"current AutoDev revision must be a full 40-character Git SHA, got {value!r}"
        )
    return value


def parse_deferred_obligations(output: str) -> list[dict[str, str]]:
    obligations: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if not line.startswith("DEFERRED:"):
            continue
        message = line[len("DEFERRED:") :].strip()
        if not message:
            continue
        lowered = message.casefold()
        platform = (
            "windows"
            if any(token in lowered for token in ("windows", "winui", "-windows"))
            else "compatible-host"
        )
        digest = hashlib.sha256(f"{platform}|{message}".encode("utf-8", errors="replace")).hexdigest()[:16]
        if digest in seen:
            continue
        seen.add(digest)
        obligations.append(
            {
                "id": digest,
                "platform": platform,
                "message": message,
                "source": "local-check",
            }
        )
    return obligations


def load_config(repo: Path) -> dict[str, object] | None:
    path = repo.expanduser().resolve() / CONFIG_PATH
    if not path.is_file():
        return None
    value = _read_json(path)
    if not isinstance(value, dict):
        raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} must contain a JSON object")
    if value.get("version") != SCHEMA_VERSION:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} version must be {SCHEMA_VERSION}"
        )
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} enabled must be boolean")
    when = str(value.get("when", "deferred-windows")).strip().casefold()
    if when not in {"deferred-windows", "always"}:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} when must be deferred-windows or always"
        )
    workflow = str(value.get("workflow", DEFAULT_CALLER_WORKFLOW)).strip()
    if enabled and (not workflow or "/" in workflow or "\\" in workflow):
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} workflow must be a workflow filename such as {DEFAULT_CALLER_WORKFLOW}"
        )
    commands = value.get("commands", [])
    if not isinstance(commands, list):
        raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} commands must be an array")
    normalized_commands: list[dict[str, str]] = []
    names: set[str] = set()
    for index, item in enumerate(commands):
        if not isinstance(item, dict):
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} commands[{index}] must be an object"
            )
        name = str(item.get("name", "")).strip()
        command = str(item.get("command", "")).strip()
        if not name or not command:
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} commands[{index}] requires name and command"
            )
        if name in names:
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} contains duplicate command name {name!r}"
            )
        names.add(name)
        normalized_commands.append({"name": name, "command": command})
    if enabled and not normalized_commands:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} enabled Windows verification requires at least one command"
        )
    timeout = value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} timeout_seconds must be a positive integer"
        )
    setup_value = value.get("setup")
    setup: dict[str, object] | None = None
    if setup_value is not None:
        if not isinstance(setup_value, dict):
            raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} setup must be an object")
        setup_name = str(setup_value.get("name", "Repository verification setup")).strip()
        setup_command = str(setup_value.get("command", "")).strip()
        if not setup_name or not setup_command:
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} setup requires a non-empty name and command"
            )
        secret_env_value = setup_value.get("secret_env", {})
        if not isinstance(secret_env_value, dict):
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} setup.secret_env must be an object"
            )
        secret_env: dict[str, str] = {}
        for environment_name, secret_name_value in secret_env_value.items():
            secret_name = str(secret_name_value).strip()
            if (
                not isinstance(environment_name, str)
                or not _ACTIONS_NAME_PATTERN.fullmatch(environment_name)
                or not _ACTIONS_NAME_PATTERN.fullmatch(secret_name)
            ):
                raise WindowsVerificationError(
                    f"{CONFIG_PATH.as_posix()} setup.secret_env must map valid environment variable names "
                    "to GitHub Actions secret names"
                )
            secret_env[environment_name] = secret_name
        setup = {
            "name": setup_name,
            "command": setup_command,
            "secret_env": secret_env,
        }
    return {
        "version": SCHEMA_VERSION,
        "enabled": enabled,
        "when": when,
        "workflow": workflow or DEFAULT_CALLER_WORKFLOW,
        "commands": normalized_commands,
        "setup": setup,
        "timeout_seconds": timeout,
    }


def validate_config(repo: Path) -> None:
    load_config(repo)


def validate_actions_installation(
    repo: Path,
    *,
    repo_full: str,
    config: dict[str, object],
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    workflow = str(config.get("workflow", DEFAULT_CALLER_WORKFLOW))
    if not repo_full:
        raise WindowsVerificationError("cannot validate Windows GitHub Actions because the target GitHub repository is unknown")

    try:
        permissions = _run(
            runner,
            ["gh", "api", f"repos/{repo_full}/actions/permissions"],
            cwd=repo,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowsVerificationError(f"could not query GitHub Actions permissions for {repo_full}: {exc}") from exc
    permissions_value = _json_stdout(permissions, "GitHub Actions permissions query")
    if isinstance(permissions_value, dict) and permissions_value.get("enabled") is False:
        raise WindowsVerificationError(
            f"GitHub Actions is disabled for {repo_full}; enable Actions before running Windows-required AutoDev verification"
        )

    try:
        view = _run(
            runner,
            ["gh", "workflow", "view", workflow, "--repo", repo_full, "--yaml"],
            cwd=repo,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WindowsVerificationError(f"could not query target Windows workflow {workflow}: {exc}") from exc
    if _returncode(view) != 0:
        detail = (_stderr(view) or _stdout(view) or "workflow not found")[-1200:]
        raise WindowsVerificationError(
            f"Windows verification requires .github/workflows/{workflow} on the default branch of {repo_full}, "
            "but GitHub cannot resolve it. Re-run the AutoDev installer, commit/merge the generated caller workflow "
            f"to the target default branch, then resume. GitHub said: {detail}"
        )
    return {
        "state": "ready",
        "transport": "github-actions",
        "workflow": workflow,
        "repo": repo_full,
    }


def safe_config_metadata(config: dict[str, object] | None) -> dict[str, object]:
    if not config:
        return {"configured": False}
    commands = config.get("commands", [])
    setup = config.get("setup")
    safe_setup = None
    if isinstance(setup, dict):
        secret_env = setup.get("secret_env", {})
        safe_setup = {
            "configured": True,
            "name": str(setup.get("name", "")),
            "secret_environment_names": sorted(secret_env) if isinstance(secret_env, dict) else [],
        }
    return {
        "configured": True,
        "enabled": bool(config.get("enabled", True)),
        "when": str(config.get("when", "deferred-windows")),
        "workflow": str(config.get("workflow", DEFAULT_CALLER_WORKFLOW)),
        "timeout_seconds": int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS),
        "command_names": [
            str(item.get("name", ""))
            for item in commands
            if isinstance(item, dict) and str(item.get("name", ""))
        ],
        "setup": safe_setup,
    }


def record_local_deferred_obligations(
    repo: Path,
    current: Path,
    state: dict[str, object],
    output: str,
) -> dict[str, object]:
    obligations = parse_deferred_obligations(output)
    config = load_config(repo)
    windows_from_log = any(item.get("platform") == "windows" for item in obligations)
    always = bool(config and config.get("enabled") and config.get("when") == "always")
    required = windows_from_log or always
    if always and not windows_from_log:
        message = "Repository Windows verification policy requires this shipped patch to run on Windows."
        obligations.append(
            {
                "id": hashlib.sha256(f"windows|{message}".encode("utf-8")).hexdigest()[:16],
                "platform": "windows",
                "message": message,
                "source": "repository-policy",
            }
        )

    state["DeferredVerificationObligations"] = obligations
    state["WindowsVerificationRequired"] = required
    state["WindowsVerificationConfig"] = safe_config_metadata(config)
    state.pop("WindowsVerificationProof", None)
    state.pop("LastWindowsVerificationFailure", None)
    _write_json(current / "state.json", state)
    _write_json(
        current / "deferred-verification.json",
        {
            "obligations": obligations,
            "windows_required": required,
            "windows_config": safe_config_metadata(config),
        },
    )
    sync_manifest(repo, state)
    return {
        "deferred_verification_obligations": obligations,
        "windows_verification_required": required,
        "windows_verification_config": safe_config_metadata(config),
    }


def windows_required(state: dict[str, object]) -> bool:
    return bool(state.get("WindowsVerificationRequired", False))


def _verification_head(state: dict[str, object]) -> str:
    return str(state.get("PrHeadSha", "")).strip() or str(state.get("LastCommitSha", "")).strip()


def proof_current(state: dict[str, object]) -> bool:
    if not windows_required(state):
        return True
    proof = state.get("WindowsVerificationProof")
    if not isinstance(proof, dict) or proof.get("state") != "terminal-success":
        return False
    head = _verification_head(state)
    source = str(state.get("ShippedSourceIdentity", "")).strip()
    return bool(
        head
        and source
        and str(proof.get("head_sha", "")) == head
        and str(proof.get("source_identity", "")) == source
    )


def current_repair_attempt(repo: Path) -> int:
    try:
        from automation import run_manifest

        path = repo.expanduser().resolve() / ".autodev-run" / "current" / run_manifest.MANIFEST_NAME
        if not path.is_file():
            return 0
        manifest = run_manifest.load_manifest(path)
        stages = manifest.get("stages", {})
        record = stages.get(MANIFEST_STAGE, {}) if isinstance(stages, dict) else {}
        details = record.get("details", {}) if isinstance(record, dict) else {}
        return int(details.get("attempt", 0) or 0) if isinstance(details, dict) else 0
    except (OSError, ValueError):
        return 0


def _list_workflow_runs(
    repo: Path,
    repo_full: str,
    workflow: str,
    branch: str,
    runner: Callable[..., object],
) -> list[dict[str, object]]:
    completed = _run(
        runner,
        [
            "gh",
            "run",
            "list",
            "--repo",
            repo_full,
            "--workflow",
            workflow,
            "--branch",
            branch,
            "--event",
            "workflow_dispatch",
            "--limit",
            "20",
            "--json",
            "databaseId,headSha,status,conclusion,url,createdAt",
        ],
        cwd=repo,
        timeout=30,
    )
    value = _json_stdout(completed, "GitHub Actions run listing")
    if not isinstance(value, list):
        raise WindowsVerificationError("GitHub Actions run listing returned a non-array JSON value")
    return [item for item in value if isinstance(item, dict)]


def _failed_logs(
    repo: Path,
    repo_full: str,
    run_id: int,
    runner: Callable[..., object],
) -> str:
    try:
        completed = _run(
            runner,
            ["gh", "run", "view", str(run_id), "--repo", repo_full, "--log-failed"],
            cwd=repo,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not retrieve failed GitHub Actions logs: {exc}"
    return (_stdout(completed) or _stderr(completed))[-MAX_CAPTURE_CHARS:]


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


# Compatibility name retained for callers/tests from the initial #120 implementation.
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


def _looks_transient_text(value: str) -> bool:
    lowered = str(value or "").casefold()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def _blocked_failure(
    repo: Path,
    current: Path,
    state: dict[str, object],
    attempt: int,
    reason: str,
) -> dict[str, object]:
    state.pop("WindowsVerificationProof", None)
    state["Status"] = "WindowsVerificationBlocked"
    state["LastWindowsVerificationFailure"] = {
        "classification": FAILURE_DETERMINISTIC,
        "attempt": attempt,
        "reason": str(reason)[:2000],
    }
    _write_json(current / "state.json", state)
    sync_manifest(repo, state)
    return {
        "state": "BLOCKED",
        "failed_stage": "windows-verification",
        "reason": str(reason)[:2000],
        "failure_classification": FAILURE_DETERMINISTIC,
        "next_action": "install/enable the target GitHub Actions Windows caller workflow, then resume",
        "artifact": str(current / "deferred-verification.json"),
        "platform_verification_stage": MANIFEST_STAGE,
        "windows_repair_attempt": attempt,
    }


def _infrastructure_failure(
    repo: Path,
    current: Path,
    state: dict[str, object],
    attempt: int,
    reason: str,
    *,
    classification: str = FAILURE_TRANSIENT,
    preserve_result: bool = False,
    run_id: int = 0,
    run_url: str = "",
) -> dict[str, object]:
    state.pop("WindowsVerificationProof", None)
    state["Status"] = "WindowsVerificationInfrastructureFailed"
    state["LastWindowsVerificationFailure"] = {
        "classification": classification,
        "attempt": attempt,
        "reason": str(reason)[:2000],
        "run_id": run_id,
        "run_url": run_url,
    }
    _write_json(current / "state.json", state)
    sync_manifest(repo, state)
    return {
        "state": "FAILED",
        "failed_stage": "windows-verification",
        "reason": str(reason)[:2000],
        "failure_classification": classification,
        "next_action": "retry or correct GitHub Actions Windows verification infrastructure, then resume",
        "artifact": str(current / RESULT_FILE) if preserve_result else str(current / REQUEST_FILE),
        "platform_verification_stage": MANIFEST_STAGE,
        "windows_repair_attempt": attempt,
    }


def _render_repair(
    current: Path,
    state: dict[str, object],
    result: dict[str, object],
    path: Path,
) -> None:
    try:
        issue = (current / "issue.md").read_text(encoding="utf-8")
    except OSError:
        issue = str(state.get("IssueText", ""))
    failures = [
        item
        for item in result.get("commands", [])
        if isinstance(item, dict) and int(item.get("returncode", 0) or 0) != 0
    ]
    evidence = json.dumps(failures, indent=2, sort_keys=True)
    _write_text(
        path,
        "# Windows verification repair\n\n"
        "Fix only the code defect demonstrated by the GitHub-hosted Windows verification lane. "
        "Do not weaken or remove Windows verification. After the repair, AutoDev will rerun deterministic, semantic, push, Windows verification, PR and CI.\n\n"
        f"## Pushed head\n{state.get('LastCommitSha', '')}\n\n"
        f"## Verified source identity\n{state.get('ShippedSourceIdentity', '')}\n\n"
        f"## GitHub Actions run\n{result.get('run_url', '')}\n\n"
        f"## Issue\n{issue.strip()}\n\n"
        f"## Failing Windows evidence\n```json\n{evidence}\n```\n",
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


def payload_metadata(state: dict[str, object]) -> dict[str, object]:
    proof = state.get("WindowsVerificationProof")
    return {
        "deferred_verification_obligations": state.get("DeferredVerificationObligations", []),
        "windows_verification_required": windows_required(state),
        "windows_verification_proof": proof if isinstance(proof, dict) else {},
    }


def sync_manifest(repo: Path, state: dict[str, object]) -> None:
    try:
        from automation import run_manifest

        path = repo.expanduser().resolve() / ".autodev-run" / "current" / run_manifest.MANIFEST_NAME
        if not path.is_file():
            return
        manifest = run_manifest.load_manifest(path)
        manifest["platform_verification"] = {
            "deferred_obligations": state.get("DeferredVerificationObligations", []),
            "windows_required": windows_required(state),
            "windows_config": state.get("WindowsVerificationConfig", {}),
            "windows_proof": state.get("WindowsVerificationProof", {}),
            "last_windows_failure": state.get("LastWindowsVerificationFailure", {}),
        }
        run_manifest.save_manifest(path, manifest)
    except (OSError, ValueError):
        return


def install_manifest_hooks() -> None:
    from automation import run_manifest

    if MANIFEST_STAGE not in run_manifest.OPTIONAL_STAGES:
        run_manifest.OPTIONAL_STAGES = (*run_manifest.OPTIONAL_STAGES, MANIFEST_STAGE)
        run_manifest.ALL_STAGES = run_manifest.PRIMARY_STAGES + run_manifest.OPTIONAL_STAGES

    if getattr(run_manifest, "_autodev_windows_invalidation_installed", False):
        return
    original = run_manifest.invalidated_stages_for_role

    def invalidated_stages_for_role(manifest: dict[str, object], role: str) -> list[str]:
        affected = list(original(manifest, role))
        completed = set(str(value) for value in manifest.get("completed_stages", []))
        if MANIFEST_STAGE in completed and (
            role == "fixer" or "pr-created" in affected or "semantic-verified" in affected
        ):
            affected.append(MANIFEST_STAGE)
        return affected

    run_manifest.invalidated_stages_for_role = invalidated_stages_for_role
    run_manifest._autodev_windows_invalidation_installed = True


def install_opencode_hooks() -> None:
    install_manifest_hooks()
    from automation import opencode_adapter, opencode_coordinator, opencode_resume, run_manifest, workflow_stages

    if getattr(opencode_resume, "_autodev_windows_hooks_installed", False):
        return

    opencode_resume.REPAIR_STAGE_KIND[MANIFEST_STAGE] = "windows"
    opencode_coordinator.REPAIR_KINDS["fixer-windows"] = "windows"

    original_repair_kind = opencode_resume._repair_kind
    original_fixer_source = opencode_adapter._fixer_source
    original_resume_action = opencode_resume.resume_action
    original_checkpoint_stage = opencode_resume.checkpoint_stage
    original_status_text = opencode_resume.status_text
    original_resume = opencode_resume.resume

    def repair_kind(arguments: str) -> str:
        if "windows" in (arguments or "").casefold():
            return "windows"
        return original_repair_kind(arguments)

    def fixer_source(current: Path, arguments: str) -> Path:
        if "windows" in (arguments or "").casefold():
            path = current / REPAIR_FILE
            if path.is_file():
                return path
            raise opencode_adapter.OpenCodeAdapterError(
                f"Windows repair artifact is missing: .autodev-run/current/{REPAIR_FILE}"
            )
        return original_fixer_source(current, arguments)

    def resume_action(manifest: dict[str, object], state: dict[str, object]) -> str:
        action = original_resume_action(manifest, state)
        if action.startswith("fixer-"):
            return action
        if windows_required(state) and run_manifest.stage_completed(manifest, "pr-created"):
            if not run_manifest.stage_completed(manifest, MANIFEST_STAGE) or not proof_current(state):
                return "pr-and-ci"
        return action

    def checkpoint_stage(repo: Path, name: str, payload: dict[str, object], attempt: int) -> None:
        if name != "pr-and-ci" or payload.get("platform_verification_stage") != MANIFEST_STAGE:
            original_checkpoint_stage(repo, name, payload, attempt)
            return

        path = opencode_resume.manifest_path(repo)
        current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
        state = workflow_stages.read_state(current)
        outcome = str(payload.get("state", ""))
        windows_attempt = int(payload.get("windows_repair_attempt", 0) or 0)
        failed_stage = str(payload.get("failed_stage", ""))
        windows_success = bool(payload.get("windows_stage_completed")) and proof_current(state)

        if failed_stage != "windows-verification":
            original_checkpoint_stage(repo, name, payload, attempt)

        if windows_success:
            artifacts = [current / RESULT_FILE] if (current / RESULT_FILE).is_file() else []
            run_manifest.complete_stage(
                path,
                MANIFEST_STAGE,
                run_root=current,
                artifacts=artifacts,
                inputs={
                    "head_sha": _verification_head(state),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                },
                details={
                    "attempt": windows_attempt,
                    "state": "terminal-success",
                    "transport": "github-actions",
                    "head_sha": _verification_head(state),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                    "run_id": int((state.get("WindowsVerificationProof", {}) or {}).get("run_id", 0)) if isinstance(state.get("WindowsVerificationProof", {}), dict) else 0,
                    "run_url": str((state.get("WindowsVerificationProof", {}) or {}).get("run_url", "")) if isinstance(state.get("WindowsVerificationProof", {}), dict) else "",
                },
            )
        elif failed_stage == "windows-verification":
            status = "repair-required" if outcome == "REPAIR" else outcome.casefold() or "failed"
            run_manifest.record_stage_state(
                path,
                MANIFEST_STAGE,
                status=status,
                details={
                    "attempt": windows_attempt,
                    "reason": str(payload.get("reason", "")),
                    "failure_classification": str(payload.get("failure_classification", "")),
                    "artifact": str(payload.get("artifact", "")),
                    "head_sha": _verification_head(state),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                },
            )
            if outcome in {"BLOCKED", "FAILED"}:
                run_manifest.record_failure(
                    path,
                    classification=str(payload.get("failure_classification", "workflow_failed")),
                    reason=str(payload.get("reason", "Windows verification stopped")),
                    stage=MANIFEST_STAGE,
                )
        sync_manifest(repo, state)

    def status_text(repo: Path, mappings: dict[str, dict[str, str]], **kwargs) -> str:
        text = original_status_text(repo, mappings, **kwargs).rstrip("\n")
        state = workflow_stages.read_state(repo.expanduser().resolve() / workflow_stages.CURRENT_DIR)
        obligations = state.get("DeferredVerificationObligations", [])
        count = len(obligations) if isinstance(obligations, list) else 0
        windows = sum(
            1
            for item in obligations
            if isinstance(item, dict) and item.get("platform") == "windows"
        ) if isinstance(obligations, list) else 0
        proof = state.get("WindowsVerificationProof", {})
        proof_state = str(proof.get("state", "")) if isinstance(proof, dict) else ""
        run_url = str(proof.get("run_url", "")) if isinstance(proof, dict) else ""
        return (
            text
            + f"\nDeferred verification obligations: {count} (windows={windows})"
            + f"\nWindows verification required: {'yes' if windows_required(state) else 'no'}"
            + f"\nWindows verification transport: GitHub Actions"
            + f"\nWindows verification proof: {proof_state or '(none)'}"
            + (f"\nWindows Actions run: {run_url}" if run_url else "")
            + "\n"
        )

    def resume(repo: Path, mappings: dict[str, dict[str, str]], **kwargs) -> dict[str, object]:
        payload = original_resume(repo, mappings, **kwargs)
        state = workflow_stages.read_state(repo.expanduser().resolve() / workflow_stages.CURRENT_DIR)
        manifest = run_manifest.load_manifest(opencode_resume.manifest_path(repo))
        attempts = opencode_resume.repair_attempts(manifest)
        payload["windows_repair_attempt"] = int(attempts.get("windows", 0) or 0)
        payload.update(payload_metadata(state))
        if payload.get("next_action") == "pr-and-ci" and windows_required(state) and run_manifest.stage_completed(manifest, "pr-created"):
            payload["next_stage"] = MANIFEST_STAGE
        return payload

    opencode_resume._repair_kind = repair_kind
    opencode_adapter._fixer_source = fixer_source
    opencode_resume.resume_action = resume_action
    opencode_resume.checkpoint_stage = checkpoint_stage
    opencode_resume.status_text = status_text
    opencode_resume.resume = resume
    opencode_resume._autodev_windows_hooks_installed = True

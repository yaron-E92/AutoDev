from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


CONFIG_PATH = Path(".autodev") / "windows-verification.json"
REQUEST_FILE = "windows-verification-request.json"
RESULT_FILE = "windows-verification-result.json"
REPAIR_FILE = "windows-repair.md"
MANIFEST_STAGE = "windows-verified"
SCHEMA_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 3600
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
)


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
    runner = value.get("runner")
    if enabled:
        if not isinstance(runner, list) or not runner or any(
            not isinstance(item, str) or not item.strip() for item in runner
        ):
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} runner must be a non-empty string array"
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
    repository_url = str(value.get("repository_url", "")).strip()
    return {
        "version": SCHEMA_VERSION,
        "enabled": enabled,
        "when": when,
        "runner": list(runner or []),
        "commands": normalized_commands,
        "timeout_seconds": timeout,
        "repository_url": repository_url,
    }


def validate_config(repo: Path) -> None:
    load_config(repo)


def safe_config_metadata(config: dict[str, object] | None) -> dict[str, object]:
    if not config:
        return {"configured": False}
    commands = config.get("commands", [])
    return {
        "configured": True,
        "enabled": bool(config.get("enabled", True)),
        "when": str(config.get("when", "deferred-windows")),
        "timeout_seconds": int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS),
        "command_names": [
            str(item.get("name", ""))
            for item in commands
            if isinstance(item, dict) and str(item.get("name", ""))
        ],
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
    windows_required = windows_from_log or always
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
    state["WindowsVerificationRequired"] = windows_required
    state["WindowsVerificationConfig"] = safe_config_metadata(config)
    state.pop("WindowsVerificationProof", None)
    state.pop("LastWindowsVerificationFailure", None)
    _write_json(current / "state.json", state)
    _write_json(
        current / "deferred-verification.json",
        {
            "obligations": obligations,
            "windows_required": windows_required,
            "windows_config": safe_config_metadata(config),
        },
    )
    sync_manifest(repo, state)
    return {
        "deferred_verification_obligations": obligations,
        "windows_verification_required": windows_required,
        "windows_verification_config": safe_config_metadata(config),
    }


def windows_required(state: dict[str, object]) -> bool:
    return bool(state.get("WindowsVerificationRequired", False))


def proof_current(state: dict[str, object]) -> bool:
    if not windows_required(state):
        return True
    proof = state.get("WindowsVerificationProof")
    if not isinstance(proof, dict) or proof.get("state") != "terminal-success":
        return False
    head = str(state.get("PrHeadSha", "")).strip()
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


def run_after_ci(
    repo: Path,
    current: Path,
    state: dict[str, object],
    *,
    max_repair_attempts: int,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object] | None:
    if not windows_required(state):
        return None

    attempt = current_repair_attempt(repo)
    config = load_config(repo)
    if not config or not bool(config.get("enabled", True)):
        reason = (
            "deferred Windows verification is required, but "
            f"{CONFIG_PATH.as_posix()} is not configured and enabled"
        )
        failure = {
            "state": "BLOCKED",
            "failed_stage": "windows-verification",
            "reason": reason,
            "failure_classification": FAILURE_DETERMINISTIC,
            "next_action": "configure an explicit Windows verification runner, then resume",
            "artifact": str(current / "deferred-verification.json"),
            "platform_verification_stage": MANIFEST_STAGE,
            "windows_repair_attempt": attempt,
        }
        state["LastWindowsVerificationFailure"] = failure
        _write_json(current / "state.json", state)
        sync_manifest(repo, state)
        return failure

    head = str(state.get("PrHeadSha", "")).strip()
    commit = str(state.get("LastCommitSha", "")).strip()
    source = str(state.get("ShippedSourceIdentity", "")).strip()
    if not head or not commit or head != commit or not source or not bool(state.get("ShippedTreeVerified")):
        raise WindowsVerificationError(
            "Windows verification refused because the shipped PR-head/source-identity proof is incomplete"
        )

    repo_full = str(state.get("RepoFullName", "")).strip()
    repository_url = str(config.get("repository_url", "")).strip()
    if not repository_url:
        repository_url = f"https://github.com/{repo_full}.git"
    request = {
        "version": SCHEMA_VERSION,
        "repo_full_name": repo_full,
        "repository_url": repository_url,
        "commit_sha": head,
        "source_identity": source,
        "obligations": [
            item
            for item in state.get("DeferredVerificationObligations", [])
            if isinstance(item, dict) and item.get("platform") == "windows"
        ],
        "commands": list(config.get("commands", [])),
    }
    request_path = current / REQUEST_FILE
    _write_json(request_path, request)

    command = [str(item) for item in config.get("runner", [])]
    timeout = int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)
    try:
        completed = runner(
            command,
            cwd=repo,
            input=json.dumps(request),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            f"Windows verification runner exceeded timeout of {timeout} seconds",
        )
    except OSError as exc:
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            f"Windows verification runner could not be launched: {exc}",
        )

    stdout = str(getattr(completed, "stdout", "") or "").strip()
    stderr = str(getattr(completed, "stderr", "") or "").strip()
    if int(getattr(completed, "returncode", 1)) != 0:
        detail = (stderr or stdout or "no runner output")[-2000:]
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            f"Windows verification runner exited nonzero: {detail}",
        )

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            "Windows verification runner did not return one JSON object",
        )
    if not isinstance(result, dict):
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            "Windows verification runner returned a non-object JSON value",
        )

    normalized = _normalized_result(result)
    result_path = current / RESULT_FILE
    _write_json(result_path, normalized)
    platform = str(normalized.get("platform", "")).casefold()
    returned_head = str(normalized.get("commit_sha", ""))
    returned_source = str(normalized.get("source_identity", ""))
    if platform != "windows" or returned_head != head or returned_source != source:
        return _infrastructure_failure(
            repo,
            current,
            state,
            attempt,
            "Windows verification identity validation failed: runner must report platform=windows and the exact requested commit/source identity",
            classification=FAILURE_DETERMINISTIC,
            preserve_result=True,
        )

    result_state = str(normalized.get("state", ""))
    if result_state == "passed":
        proof = {
            "state": "terminal-success",
            "platform": "windows",
            "head_sha": head,
            "source_identity": source,
            "result_sha256": _sha256_file(result_path),
            "command_names": [
                str(item.get("name", ""))
                for item in normalized.get("commands", [])
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
            "next_action": "mark the PR ready for human review",
            "artifact": str(result_path),
            "platform_verification_stage": MANIFEST_STAGE,
            "windows_verification_proof": proof,
            "windows_repair_attempt": attempt,
        }

    if result_state == "code-failure":
        if _result_looks_transient(normalized):
            return _infrastructure_failure(
                repo,
                current,
                state,
                attempt,
                "Windows verification command failed with transient infrastructure evidence",
                preserve_result=True,
            )
        repair_path = current / REPAIR_FILE
        _render_repair(current, state, normalized, repair_path)
        state.pop("WindowsVerificationProof", None)
        state["Status"] = "WindowsVerificationFailed"
        state["LastWindowsVerificationFailure"] = {
            "classification": FAILURE_CODE_REPAIRABLE,
            "attempt": attempt,
            "head_sha": head,
            "source_identity": source,
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
            "reason": "Windows-only verification failed on the shipped PR head",
            "failure_classification": FAILURE_CODE_REPAIRABLE,
            "next_action": "delegate the Windows repair to autodev-fixer, then rerun local, semantic, PR/CI, and Windows verification",
            "artifact": str(repair_path),
            "platform_verification_stage": MANIFEST_STAGE,
            "windows_repair_attempt": attempt,
        }

    reason = str(normalized.get("reason", "")).strip() or "Windows verification worker reported infrastructure failure"
    return _infrastructure_failure(
        repo,
        current,
        state,
        attempt,
        reason,
        preserve_result=True,
    )


def _normalized_result(result: dict[str, object]) -> dict[str, object]:
    commands: list[dict[str, object]] = []
    raw_commands = result.get("commands", [])
    if isinstance(raw_commands, list):
        for item in raw_commands:
            if not isinstance(item, dict):
                continue
            commands.append(
                {
                    "name": str(item.get("name", ""))[:200],
                    "returncode": int(item.get("returncode", 0) or 0),
                    "output": str(item.get("output", ""))[-MAX_CAPTURE_CHARS:],
                }
            )
    return {
        "version": int(result.get("version", SCHEMA_VERSION) or SCHEMA_VERSION),
        "state": str(result.get("state", "")),
        "platform": str(result.get("platform", "")),
        "commit_sha": str(result.get("commit_sha", "")),
        "source_identity": str(result.get("source_identity", "")),
        "reason": str(result.get("reason", ""))[-4000:],
        "commands": commands,
    }


def _result_looks_transient(result: dict[str, object]) -> bool:
    text = str(result.get("reason", ""))
    commands = result.get("commands", [])
    if isinstance(commands, list):
        text += " " + " ".join(
            str(item.get("output", ""))
            for item in commands
            if isinstance(item, dict) and int(item.get("returncode", 0) or 0) != 0
        )
    lowered = text.casefold()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def _infrastructure_failure(
    repo: Path,
    current: Path,
    state: dict[str, object],
    attempt: int,
    reason: str,
    *,
    classification: str = FAILURE_TRANSIENT,
    preserve_result: bool = False,
) -> dict[str, object]:
    state.pop("WindowsVerificationProof", None)
    state["Status"] = "WindowsVerificationInfrastructureFailed"
    state["LastWindowsVerificationFailure"] = {
        "classification": classification,
        "attempt": attempt,
        "reason": str(reason)[:2000],
    }
    _write_json(current / "state.json", state)
    sync_manifest(repo, state)
    return {
        "state": "FAILED",
        "failed_stage": "windows-verification",
        "reason": str(reason)[:2000],
        "failure_classification": classification,
        "next_action": "correct or retry the Windows verification infrastructure, then resume",
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
    issue = ""
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
        "Fix only the code defect demonstrated by the real Windows verification lane. "
        "Do not weaken or remove Windows verification. After the repair, AutoDev will rerun deterministic, semantic, PR/CI, and Windows verification.\n\n"
        f"## Shipped head\n{state.get('PrHeadSha', '')}\n\n"
        f"## Shipped source identity\n{state.get('ShippedSourceIdentity', '')}\n\n"
        f"## Issue\n{issue.strip()}\n\n"
        f"## Failing Windows command evidence\n```json\n{evidence}\n```\n",
    )


def validate_ready(current: Path, state: dict[str, object]) -> None:
    if not windows_required(state):
        return
    if not proof_current(state):
        raise WindowsVerificationError(
            "ready refused: deferred Windows verification is required but no current terminal-success proof exists"
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
        or str(result.get("commit_sha", "")) != str(state.get("PrHeadSha", ""))
        or str(result.get("source_identity", "")) != str(state.get("ShippedSourceIdentity", ""))
    ):
        raise WindowsVerificationError(
            "ready refused: Windows verification result is not bound to the current shipped source"
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

        ci_payload = dict(payload)
        ci_payload["state"] = "CONTINUE"
        ci_payload["failed_stage"] = ""
        ci_payload["failure_classification"] = ""
        original_checkpoint_stage(repo, name, ci_payload, attempt)

        path = opencode_resume.manifest_path(repo)
        current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
        state = workflow_stages.read_state(current)
        outcome = str(payload.get("state", ""))
        windows_attempt = int(payload.get("windows_repair_attempt", 0) or 0)
        if outcome == "CONTINUE":
            artifacts = [current / RESULT_FILE] if (current / RESULT_FILE).is_file() else []
            run_manifest.complete_stage(
                path,
                MANIFEST_STAGE,
                run_root=current,
                artifacts=artifacts,
                inputs={
                    "head_sha": str(state.get("PrHeadSha", "")),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                },
                details={
                    "attempt": windows_attempt,
                    "state": "terminal-success",
                    "head_sha": str(state.get("PrHeadSha", "")),
                    "source_identity": str(state.get("ShippedSourceIdentity", "")),
                },
            )
        else:
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
                    "head_sha": str(state.get("PrHeadSha", "")),
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
        return (
            text
            + f"\nDeferred verification obligations: {count} (windows={windows})"
            + f"\nWindows verification required: {'yes' if windows_required(state) else 'no'}"
            + f"\nWindows verification proof: {proof_state or '(none)'}\n"
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

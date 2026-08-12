from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from automation import opencode_adapter, opencode_resume, workflow_stages


SUPPORTED_ROOT_OPENCODE_CONFIG = {"opencode.json", "opencode.jsonc"}
WORKSPACE_SNAPSHOT_FILES = {"workspace-snapshot.json", "last-commit-workspace-snapshot.json"}
FRONTEND_FAILURE_FILE = "opencode-last-failure.json"
ROLE_CHECK_COMMAND = "role-check"


def install_workflow_guards() -> None:
    """Apply OpenCode-frontend-only workspace rules before invoking shared stages."""
    current = workflow_stages.ignored_workspace_path
    if not getattr(current, "_autodev_opencode_guard", False):
        original = current

        def ignored_workspace_path(relative: str) -> bool:
            normalized = relative.replace("\\", "/").removeprefix("./")
            if normalized in SUPPORTED_ROOT_OPENCODE_CONFIG:
                return True
            return original(relative)

        ignored_workspace_path._autodev_opencode_guard = True  # type: ignore[attr-defined]
        workflow_stages.ignored_workspace_path = ignored_workspace_path

    current_read_json = workflow_stages.read_json
    if getattr(current_read_json, "_autodev_opencode_snapshot_guard", False):
        return
    original_read_json = current_read_json

    def read_json(path: Path):
        value = original_read_json(path)
        if Path(path).name not in WORKSPACE_SNAPSHOT_FILES or not isinstance(value, dict):
            return value
        return {
            str(relative): digest
            for relative, digest in value.items()
            if not workflow_stages.ignored_workspace_path(str(relative))
        }

    read_json._autodev_opencode_snapshot_guard = True  # type: ignore[attr-defined]
    workflow_stages.read_json = read_json


def _failure_path(repo: Path) -> Path:
    return repo / workflow_stages.CURRENT_DIR / FRONTEND_FAILURE_FILE


def _clear_failure(repo: Path) -> None:
    _failure_path(repo).unlink(missing_ok=True)


def _persist_failure(repo: Path, payload: dict[str, object]) -> None:
    path = _failure_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "issue_number": int(payload.get("issue_number", 0) or 0),
        "branch": str(payload.get("branch", "")),
        "completed_stage": str(payload.get("completed_stage", "")),
        "failed_stage": str(payload.get("failed_stage", "")),
        "reason": str(payload.get("reason", "")),
        "failure_classification": str(payload.get("failure_classification", "")),
        "failure_fingerprint": str(payload.get("failure_fingerprint", "")),
    }
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_persisted_failure(repo: Path) -> dict[str, object]:
    try:
        value = json.loads(_failure_path(repo).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _last_json_object(text: str) -> dict[str, object]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _run_adapter(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = opencode_adapter.run(argv)
    out = stdout.getvalue()
    err = stderr.getvalue()
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code, out, err


def _terminal_failed(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    persisted = _read_persisted_failure(repo)
    diagnostics = workflow_stages.read_json(current / workflow_stages.DIAGNOSTICS_FILE)
    last_failure = diagnostics.get("last_failure", {}) if isinstance(diagnostics, dict) else {}
    if not isinstance(last_failure, dict):
        last_failure = {}
    state_value = workflow_stages.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}

    issue_number = int(
        persisted.get("issue_number", 0)
        or state.get("IssueNumber", 0)
        or workflow_stages.issue_number_from_arguments(args.arguments)
        or 0
    )
    failed_stage = str(
        persisted.get("failed_stage", "")
        or last_failure.get("stage", "")
        or "failed"
    )
    reason = str(
        persisted.get("reason", "")
        or last_failure.get("reason", "")
        or args.reason
        or "OpenCode coordinator failed"
    )
    classification = str(
        persisted.get("failure_classification", "")
        or last_failure.get("classification", "")
        or workflow_stages.FAILURE_DETERMINISTIC
    )
    fingerprint = str(
        persisted.get("failure_fingerprint", "")
        or last_failure.get("fingerprint", "")
    )

    if state:
        workflow_stages.mark_blocked(current, state, reason)

    payload = workflow_stages.stage_payload(
        repo,
        "FAILED",
        failed_stage,
        reason=reason,
        requested_issue=issue_number,
        next_action="inspect the originating failure artifacts, correct that failure, then resume or restart intentionally",
        failure_classification=classification,
        failure_fingerprint=fingerprint,
    )
    payload["stage"] = "failed"
    payload["failed_stage"] = failed_stage
    if persisted.get("branch"):
        payload["branch"] = str(persisted["branch"])
    if persisted.get("completed_stage"):
        payload["completed_stage"] = str(persisted["completed_stage"])
    if opencode_resume.has_manifest(repo):
        opencode_resume.checkpoint_stage(repo, "failed", payload, 0)
    print(json.dumps(payload, sort_keys=True))
    return 0


def _artifact_evidence(current: Path, relative: str) -> dict[str, object] | None:
    if not relative.startswith(".autodev-run/current/"):
        return None
    path = current / Path(relative).name
    try:
        data = path.read_bytes()
    except OSError:
        return {"artifact": relative, "exists": False, "bytes": 0}
    return {"artifact": relative, "exists": True, "bytes": len(data)}


def _headroom_expected() -> bool:
    injected = os.environ.get("OPENCODE_CONFIG_CONTENT", "")
    return bool(
        os.environ.get("HEADROOM_PORT", "").strip()
        or os.environ.get("HEADROOM_WORKSPACE_DIR", "").strip()
        or '"headroom"' in injected.casefold()
    )


def _headroom_diagnostics(provider: str) -> dict[str, object]:
    expected = _headroom_expected()
    result: dict[str, object] = {
        "expected": expected,
        "routing": "not-requested" if not expected else ("proxy" if provider == "headroom" else "bypassed"),
        "proxy_reachable": False,
        "proxy_status": "not-checked" if not expected else "unreachable",
        "proxy_ready": False,
        "kompress_status": "unknown",
        "kompress_ready": None,
    }
    if not expected:
        return result

    raw_port = os.environ.get("HEADROOM_PORT", "").strip() or "8787"
    try:
        port = int(raw_port)
    except ValueError:
        result["proxy_status"] = "invalid-port"
        return result

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
            raw = response.read(64_000)
    except (OSError, urllib.error.URLError, ValueError):
        return result

    result["proxy_reachable"] = True
    try:
        health = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        result["proxy_status"] = "invalid-health-json"
        return result
    if not isinstance(health, dict):
        result["proxy_status"] = "invalid-health-json"
        return result

    result["proxy_status"] = str(health.get("status", "unknown"))
    result["proxy_ready"] = bool(health.get("ready", False))
    candidates = [health.get("kompress")]
    checks = health.get("checks", {})
    if isinstance(checks, dict):
        candidates.append(checks.get("kompress"))
    for value in candidates:
        if isinstance(value, dict):
            result["kompress_status"] = str(value.get("status", "unknown"))
            result["kompress_ready"] = bool(value.get("ready", False))
            break
    return result


def _role_diagnostics(repo: Path, role: str) -> dict[str, object]:
    current = repo / workflow_stages.CURRENT_DIR
    contract = opencode_adapter.role_contracts().get(role, {})
    model = ""
    source = ""
    try:
        mapping = opencode_adapter.resolve_opencode_model_mappings(repo).get(role, {})
        model = str(mapping.get("model", ""))
        source = str(mapping.get("source", ""))
    except (OSError, ValueError, opencode_adapter.OpenCodeAdapterError):
        pass

    inputs: list[dict[str, object]] = []
    for key in ("input_artifact", "template_artifact"):
        relative = str(contract.get(key, ""))
        evidence = _artifact_evidence(current, relative)
        if evidence is not None:
            inputs.append(evidence)

    provider = model.split("/", 1)[0] if "/" in model else ""
    return {
        "provider": provider,
        "model": model,
        "model_source": source,
        "input_artifacts": inputs,
        "expected_output": str(contract.get("output_artifact", "")),
        "headroom": _headroom_diagnostics(provider),
    }


def _role_check(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="autodev role-check")
    parser.add_argument("--role", choices=opencode_adapter.ROLE_NAMES, required=True)
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    diagnostics = _role_diagnostics(repo, args.role)
    state_value = workflow_stages.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    accepted = state.get("AcceptedRoleArtifacts", {})
    entry = accepted.get(args.role) if isinstance(accepted, dict) else None
    if not isinstance(entry, dict):
        print(
            json.dumps(
                {
                    "state": "MISSING",
                    "role": args.role,
                    "reason": "role has no durable accepted artifact/state; inspect the child Task/provider failure",
                    "diagnostics": diagnostics,
                },
                sort_keys=True,
            )
        )
        return 1

    artifact = str(entry.get("artifact", ""))
    expected = str(entry.get("sha256", ""))
    if artifact.startswith(".autodev-run/current/"):
        path = current / Path(artifact).name
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            actual = ""
        if not actual or actual != expected:
            print(
                json.dumps(
                    {
                        "state": "STALE",
                        "role": args.role,
                        "artifact": artifact,
                        "reason": "accepted role artifact is missing or no longer matches its durable hash",
                        "diagnostics": diagnostics,
                    },
                    sort_keys=True,
                )
            )
            return 1

    print(
        json.dumps(
            {
                "state": "ACCEPTED",
                "role": args.role,
                "artifact": artifact,
                "sha256": expected,
                "diagnostics": diagnostics,
            },
            sort_keys=True,
        )
    )
    return 0


def run(argv: list[str] | None = None) -> int:
    install_workflow_guards()
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == ROLE_CHECK_COMMAND:
        return _role_check(values[1:])

    args = opencode_adapter.build_parser().parse_args(values)

    if args.command == "stage" and args.name == "failed":
        return _terminal_failed(args)

    code, out, _ = _run_adapter(values)
    if args.command == "stage":
        payload = _last_json_object(out)
        repo = Path(args.repo).expanduser().resolve()
        if code != 0 and payload.get("state") == "FAILED":
            _persist_failure(repo, payload)
        elif code == 0 and payload.get("state") not in {"FAILED", "BLOCKED", "REPAIR"}:
            _clear_failure(repo)
    return code


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

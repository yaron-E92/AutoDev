from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from automation import opencode_adapter, opencode_resume, workflow_stages


SUPPORTED_ROOT_OPENCODE_CONFIG = {"opencode.json", "opencode.jsonc"}
FRONTEND_FAILURE_FILE = "opencode-last-failure.json"


def install_workflow_guards() -> None:
    """Apply OpenCode-frontend-only workspace rules before invoking shared stages."""
    current = workflow_stages.ignored_workspace_path
    if getattr(current, "_autodev_opencode_guard", False):
        return

    original = current

    def ignored_workspace_path(relative: str) -> bool:
        normalized = relative.replace("\\", "/").removeprefix("./")
        if normalized in SUPPORTED_ROOT_OPENCODE_CONFIG:
            return True
        return original(relative)

    ignored_workspace_path._autodev_opencode_guard = True  # type: ignore[attr-defined]
    workflow_stages.ignored_workspace_path = ignored_workspace_path


def _failure_path(repo: Path) -> Path:
    return repo / workflow_stages.CURRENT_DIR / FRONTEND_FAILURE_FILE


def _persist_failure(repo: Path, payload: dict[str, object]) -> None:
    path = _failure_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "issue_number": int(payload.get("issue_number", 0) or 0),
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
    if opencode_resume.has_manifest(repo):
        opencode_resume.checkpoint_stage(repo, "failed", payload, 0)
    print(json.dumps(payload, sort_keys=True))
    return 0


def run(argv: list[str] | None = None) -> int:
    install_workflow_guards()
    values = list(sys.argv[1:] if argv is None else argv)
    args = opencode_adapter.build_parser().parse_args(values)

    if args.command == "stage" and args.name == "failed":
        return _terminal_failed(args)

    code, out, _ = _run_adapter(values)
    if args.command == "stage" and code != 0:
        payload = _last_json_object(out)
        if payload.get("state") == "FAILED":
            _persist_failure(Path(args.repo).expanduser().resolve(), payload)
    return code


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

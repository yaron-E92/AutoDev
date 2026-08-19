from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from automation import (
    github_cli_proxy,
    non_success_report,
    opencode_adapter,
    opencode_failure_entrypoint,
    role_coordinator as opencode_coordinator,
    role_resume,
    role_runtime,
    workflow_stages,
)


def diagnostic_runner(command, *args, **kwargs):
    completed = opencode_failure_entrypoint.classified_runner(command, *args, **kwargs)
    if (
        isinstance(command, (list, tuple))
        and command
        and str(command[0]) == "gh"
        and int(getattr(completed, "returncode", 0)) != 0
    ):
        stderr = str(getattr(completed, "stderr", "") or "")
        label = github_cli_proxy.operation_label([str(value) for value in command[1:]])
        hint = github_cli_proxy.workflow_authorization_hint(stderr)
        detail = f"AutoDev GitHub operation failed: {label}: {stderr or 'no command output'}"
        if hint:
            detail += hint
        try:
            completed.stderr = detail
        except (AttributeError, TypeError):
            pass
    return completed


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodev coordinate")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--runtime",
        default="",
        help="role runtime override (default: configured runtime, then opencode)",
    )
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    proxy = opencode_failure_entrypoint.JsonEventProxy(sys.stdout, repo)

    original_stdout = sys.stdout
    sys.stdout = proxy
    try:
        try:
            payload = opencode_coordinator.coordinate(
                repo,
                arguments=args.arguments,
                resume=args.resume,
                invalidated_roles=(
                    opencode_coordinator.invalidations(args.arguments)
                    if args.resume
                    else set()
                ),
                runtime_name=args.runtime,
                runner=diagnostic_runner,
            )
        except (
            opencode_failure_entrypoint.ProviderCapabilityError,
            opencode_coordinator.RoleCoordinatorError,
            role_runtime.RoleRuntimeError,
            role_resume.RoleResumeError,
            opencode_adapter.OpenCodeAdapterError,
            workflow_stages.WorkflowStageError,
            OSError,
            ValueError,
        ) as exc:
            payload = opencode_coordinator.terminal_payload(
                repo,
                {
                    "state": "FAILED",
                    "reason": str(exc),
                    "failed_stage": "python-coordinator",
                    "failure_classification": str(
                        getattr(exc, "classification", "")
                        or workflow_stages.FAILURE_DETERMINISTIC
                    ),
                    "artifact": str(getattr(exc, "diagnostic_path", "") or ""),
                },
                arguments=args.arguments,
            )
    finally:
        proxy.flush()
        sys.stdout = original_stdout

    if (
        proxy.last_local_payload is not None
        and payload.get("failed_stage") == "local-check"
        and not payload.get("failure_fingerprint")
    ):
        payload = dict(payload)
        payload["failure_fingerprint"] = proxy.last_local_payload.get(
            "failure_fingerprint", ""
        )
        payload["repeated_failure"] = proxy.last_local_payload.get(
            "repeated_failure", False
        )

    payload, report_path = non_success_report.update_report(repo, payload)
    if report_path:
        print(f"AutoDev report: {report_path}", flush=True)
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if payload.get("state") in {"PR_READY", "BLOCKED", "WAITING"} else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

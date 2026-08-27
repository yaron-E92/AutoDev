from __future__ import annotations

from automation import opencode_adapter_models

from automation import opencode_adapter_contract

import argparse
import json
import sys
from pathlib import Path

from automation import (
    github_cli_proxy,
    notification_outcomes,
    non_success_report,
    opencode_failure_entrypoint,
    role_resume,
    role_runtime,
    workflow_stages,
)

from automation import (
    role_coordinator_cli,
    role_coordinator_contract,
    role_coordinator_flow,
    role_coordinator_stages,
)

SUCCESSFUL_TERMINAL_STATES = {
    "PR_READY",
    "BLOCKED",
    "WAITING",
    "ATTENTION_REQUIRED",
}


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
            selected_name, _ = role_runtime.resolve_runtime_name(repo, args.runtime)
            if selected_name == "opencode":
                opencode_adapter_models.reject_unsupported_model_overrides(args.arguments)
            payload = role_coordinator_flow.coordinate(
                repo,
                arguments=args.arguments,
                resume=args.resume,
                invalidated_roles=(
                    role_coordinator_cli.invalidations(args.arguments)
                    if args.resume
                    else set()
                ),
                runtime_name=args.runtime,
                runner=diagnostic_runner,
            )
        except (
            opencode_failure_entrypoint.ProviderCapabilityError,
            role_coordinator_contract.RoleCoordinatorError,
            role_runtime.RoleRuntimeError,
            role_resume.RoleResumeError,
            opencode_adapter_contract.OpenCodeAdapterError,
            workflow_stages.WorkflowStageError,
            OSError,
            ValueError,
        ) as exc:
            payload = role_coordinator_stages.terminal_payload(
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

    # ATTENTION_REQUIRED is a successful non-runnable classification, not a
    # failed run. If this run previously failed before being reclassified, do
    # not leave that obsolete diagnostic next to the current attention state.
    if payload.get("state") == "ATTENTION_REQUIRED":
        (
            repo
            / workflow_stages.CURRENT_DIR
            / non_success_report.REPORT_NAME
        ).unlink(missing_ok=True)

    payload, report_path = non_success_report.update_report(repo, payload)
    notification_outcomes.best_effort_notify_run_outcome(
        repo,
        payload,
        runner=diagnostic_runner,
    )
    if report_path:
        print(f"AutoDev report: {report_path}", flush=True)
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if payload.get("state") in SUCCESSFUL_TERMINAL_STATES else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
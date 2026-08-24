from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from automation import (
    opencode_adapter,
    opencode_runtime,
    role_resume,
    role_runtime,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation import coordination_contract, coordination_state

from automation.role_coordinator_contract import (
    RoleCoordinatorError,
)
from automation.role_coordinator_flow import (
    coordinate,
)
from automation.role_coordinator_stages import (
    terminal_payload,
)

def invalidations(arguments: str) -> set[str]:
    return coordination_state.invalidated_roles(
        arguments,
        roles=tuple(opencode_adapter.ROLE_NAMES),
        error_type=RoleCoordinatorError,
    )

def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodev coordinate")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--runtime", default="")
    args = parser.parse_args(argv)
    try:
        payload = coordinate(
            Path(args.repo),
            arguments=args.arguments,
            resume=args.resume,
            invalidated_roles=invalidations(args.arguments) if args.resume else set(),
            runtime_name=args.runtime,
        )
    except (
        RoleCoordinatorError,
        role_runtime.RoleRuntimeError,
        role_resume.RoleResumeError,
        opencode_adapter.OpenCodeAdapterError,
        workflow_stages.WorkflowStageError,
        OSError,
        ValueError,
    ) as exc:
        diagnostic_path = str(getattr(exc, "diagnostic_path", "") or "")
        payload = terminal_payload(
            Path(args.repo).expanduser().resolve(),
            {
                "state": "FAILED",
                "reason": str(exc),
                "failed_stage": "python-coordinator",
                "failure_classification": str(
                    getattr(exc, "classification", "")
                    or workflow_stages.FAILURE_DETERMINISTIC
                ),
                "artifact": diagnostic_path,
            },
            arguments=args.arguments,
        )
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if payload.get("state") in {"PR_READY", "BLOCKED"} else 1

def main() -> int:
    return run()

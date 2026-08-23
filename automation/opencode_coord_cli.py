from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path
from automation import (
    opencode_adapter,
    opencode_cli,
    opencode_resume,
    opencode_runtime,
    privacy,
    role_runtime_diagnostics,
    workflow_stages,
)
from automation import coordination_contract, coordination_state

from automation.opencode_coord_contract import (
    OpenCodeCoordinatorError,
)
from automation.opencode_coord_flow import (
    coordinate,
)
from automation.opencode_coord_stages import (
    terminal_payload,
)

def invalidations(arguments: str) -> set[str]:
    return coordination_state.invalidated_roles(
        arguments,
        roles=tuple(opencode_adapter.ROLE_NAMES),
        error_type=OpenCodeCoordinatorError,
    )

def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodev coordinate")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = coordinate(
            Path(args.repo),
            arguments=args.arguments,
            resume=args.resume,
            invalidated_roles=invalidations(args.arguments) if args.resume else set(),
        )
    except (
        OpenCodeCoordinatorError,
        opencode_adapter.OpenCodeAdapterError,
        opencode_resume.OpenCodeResumeError,
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
                    getattr(exc, "classification", "") or workflow_stages.FAILURE_DETERMINISTIC
                ),
                "artifact": diagnostic_path,
            },
            arguments=args.arguments,
        )
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if payload.get("state") in {"PR_READY", "BLOCKED"} else 1

def main() -> int:
    return run()

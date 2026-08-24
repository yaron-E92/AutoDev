from __future__ import annotations

from automation import opencode_adapter_roles

from automation import opencode_adapter_contract

import argparse
import json
import subprocess
from pathlib import Path

from automation import (
    opencode_role_runtime,
    opencode_runtime,
    role_coordinator_contract,
    role_coordinator_runtime,
    role_resume,
    role_runtime,
    workflow_stages,
)


ROLE_NAMES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one standalone AutoDev OpenCode role through the privacy-gated role runtime."
    )
    parser.add_argument("--role", choices=ROLE_NAMES, required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    opencode_runtime.install_workflow_guards()
    runtime = opencode_role_runtime.OpenCodeRoleRuntime()
    try:
        runtime.validate_arguments(args.arguments)
        snapshots = runtime.role_snapshots(repo, runner=subprocess.run)
        opencode_adapter_roles.prepare_role(args.role, repo, args.arguments)
        acceptance = role_coordinator_runtime.run_role(
            repo,
            args.role,
            runtime,
            snapshots,
            already_prepared=True,
            runner=subprocess.run,
        )
        payload = {
            "state": "ACCEPTED",
            "role": args.role,
            "runtime": runtime.name,
            "artifact": str(acceptance.get("artifact", "")),
        }
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 0
    except (
        opencode_adapter_contract.OpenCodeAdapterError,
        role_coordinator_contract.RoleCoordinatorError,
        role_runtime.RoleRuntimeError,
        role_resume.RoleResumeError,
        workflow_stages.WorkflowStageError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "state": "FAILED",
            "role": args.role,
            "runtime": runtime.name,
            "classification": str(
                getattr(exc, "classification", "") or "deterministic"
            ),
            "reason": str(exc),
        }
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from automation import opencode_adapter, opencode_coordinator, opencode_runtime, privacy, workflow_stages


ROLE_NAMES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one standalone AutoDev OpenCode role through the privacy-gated subprocess path."
    )
    parser.add_argument("--role", choices=ROLE_NAMES, required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    opencode_runtime.install_workflow_guards()
    try:
        opencode_adapter.prepare_role(args.role, repo, args.arguments)
        acceptance = opencode_coordinator.run_role(
            repo,
            args.role,
            already_prepared=True,
            runner=subprocess.run,
        )
        payload = {
            "state": "ACCEPTED",
            "role": args.role,
            "artifact": str(acceptance.get("artifact", "")),
        }
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 0
    except (
        opencode_adapter.OpenCodeAdapterError,
        opencode_coordinator.OpenCodeCoordinatorError,
        privacy.PrivacyError,
        workflow_stages.WorkflowStageError,
        OSError,
        ValueError,
    ) as exc:
        payload = {
            "state": "FAILED",
            "role": args.role,
            "classification": str(getattr(exc, "classification", "") or "deterministic"),
            "reason": str(exc),
        }
        print(json.dumps(payload, sort_keys=True), flush=True)
        return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

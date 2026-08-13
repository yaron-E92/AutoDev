from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from automation import (
    failure_diagnostics,
    opencode_adapter,
    opencode_coordinator,
    opencode_resume,
    workflow_stages,
)


class ProviderCapabilityError(ValueError):
    classification = failure_diagnostics.FAILURE_PROVIDER_CAPABILITY


def _bounded_detail(completed: object) -> str:
    stderr = str(getattr(completed, "stderr", "") or "").strip()
    stdout = str(getattr(completed, "stdout", "") or "").strip()
    return (stderr or stdout)[-2000:]


def classified_runner(command, *args, **kwargs):
    completed = subprocess.run(command, *args, **kwargs)
    if int(getattr(completed, "returncode", 0)) == 0:
        return completed
    detail = _bounded_detail(completed)
    classification = failure_diagnostics.classify_provider_failure(detail, "")
    if classification == failure_diagnostics.FAILURE_PROVIDER_CAPABILITY:
        raise ProviderCapabilityError(
            "provider rejected the OpenCode request as too large for the configured limit"
            + (f": {detail}" if detail else "")
        )
    return completed


def _augment_local_failure(repo: Path, payload: dict[str, object]) -> dict[str, object]:
    if payload.get("stage") != "local-check" or payload.get("state") not in {
        "REPAIR",
        "BLOCKED",
        "FAILED",
    }:
        return payload

    current = repo / workflow_stages.CURRENT_DIR
    state_value = workflow_stages.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    command = str(state.get("LocalCheck", ""))
    evidence = workflow_stages.read_text(current / "local-check.log")
    fingerprint = failure_diagnostics.local_failure_fingerprint(command, evidence, repo)
    if not fingerprint:
        return payload

    diagnostics = workflow_stages._diagnostics(current)
    previous = diagnostics.get("last_failure", {})
    repeated = bool(
        isinstance(previous, dict)
        and previous.get("stage") == "local-check"
        and previous.get("fingerprint") == fingerprint
    )
    if repeated:
        diagnostics["repeated_identical_failures"] = int(
            diagnostics.get("repeated_identical_failures", 0) or 0
        ) + 1
    failures = diagnostics.setdefault("failure_fingerprints", {})
    if isinstance(failures, dict):
        failures[fingerprint] = int(failures.get(fingerprint, 0) or 0) + 1
    diagnostics["last_failure"] = {
        "stage": "local-check",
        "classification": str(
            payload.get("failure_classification", "")
            or workflow_stages.FAILURE_CODE_REPAIRABLE
        ),
        "reason": str(payload.get("reason", "")),
        "fingerprint": fingerprint,
        "input_fingerprint": workflow_stages._stage_input_fingerprint(repo, "local-check"),
    }
    workflow_stages._write_diagnostics(current, diagnostics)

    updated = dict(payload)
    updated["failure_fingerprint"] = fingerprint
    updated["repeated_failure"] = repeated
    values = updated.get("diagnostics", {})
    if isinstance(values, dict):
        values = dict(values)
        values["repeated_identical_failures"] = diagnostics.get(
            "repeated_identical_failures", 0
        )
        updated["diagnostics"] = values
    return updated


class JsonEventProxy:
    def __init__(self, target, repo: Path) -> None:
        self.target = target
        self.repo = repo
        self.buffer = ""
        self.last_local_payload: dict[str, object] | None = None

    def write(self, value: str) -> int:
        self.buffer += value
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._write_line(line)
        return len(value)

    def _write_line(self, line: str) -> None:
        rendered = line
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and value.get("event") == "stage":
            value = _augment_local_failure(self.repo, value)
            if value.get("stage") == "local-check" and value.get("failure_fingerprint"):
                self.last_local_payload = value
            rendered = json.dumps(value, sort_keys=True)
        self.target.write(rendered + "\n")
        self.target.flush()

    def flush(self) -> None:
        if self.buffer:
            self._write_line(self.buffer)
            self.buffer = ""
        self.target.flush()


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodev coordinate")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--arguments", default="")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    proxy = JsonEventProxy(sys.stdout, repo)

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
                runner=classified_runner,
            )
        except (
            ProviderCapabilityError,
            opencode_coordinator.OpenCodeCoordinatorError,
            opencode_adapter.OpenCodeAdapterError,
            opencode_resume.OpenCodeResumeError,
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
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0 if payload.get("state") in {"PR_READY", "BLOCKED"} else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

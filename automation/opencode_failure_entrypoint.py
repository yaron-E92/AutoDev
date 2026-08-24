from __future__ import annotations

import json
import subprocess
from pathlib import Path

from automation import (
    failure_diagnostics,
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

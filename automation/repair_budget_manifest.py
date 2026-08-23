from __future__ import annotations

from pathlib import Path
from automation import run_manifest

from automation.repair_budget_contract import (
    FAILURE_REPAIR_BUDGET_EXHAUSTED,
)

def install_run_manifest_hooks() -> None:
    if getattr(run_manifest.record_failure, "_autodev_semantic_budget_hook", False):
        return
    original = run_manifest.record_failure

    def record_failure(path: Path, *, classification: str, reason: str, stage: str = ""):
        rich: dict[str, object] = {}
        try:
            before = run_manifest.load_manifest(path).get("failure", {})
            if (
                isinstance(before, dict)
                and before.get("classification") == FAILURE_REPAIR_BUDGET_EXHAUSTED
                and classification == FAILURE_REPAIR_BUDGET_EXHAUSTED
            ):
                rich = {
                    key: before[key]
                    for key in ("root_classification", "fingerprint", "details")
                    if key in before
                }
        except run_manifest.ManifestError:
            pass
        manifest = original(
            path,
            classification=classification,
            reason=reason,
            stage=stage,
        )
        if rich:
            failure = manifest.get("failure", {})
            if isinstance(failure, dict):
                failure.update(rich)
            run_manifest.save_manifest(path, manifest)
        return manifest

    setattr(record_failure, "_autodev_semantic_budget_hook", True)
    run_manifest.record_failure = record_failure

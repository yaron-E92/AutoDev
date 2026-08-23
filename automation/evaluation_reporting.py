from __future__ import annotations

import json
from pathlib import Path

from automation.evaluation_contract import (
    SCHEMA_VERSION,
    UNKNOWN,
    utc_now,
)
from automation.evaluation_profiles import (
    redact,
)

def aggregate(results: list[dict[str, object]], cases: dict[str, dict[str, object]]) -> dict[str, object]:
    profiles: dict[str, dict[str, object]] = {}
    tags: dict[str, dict[str, int]] = {}
    for result in results:
        name = str(result["profile"])
        profile = profiles.setdefault(
            name,
            {
                "cases": 0,
                "completed": 0,
                "deterministic_passes": 0,
                "semantic_passes": 0,
                "files_changed": 0,
                "model_calls": 0,
                "provider_failures": 0,
                "reported_cost": 0.0,
                "reported_cost_known_cases": 0,
            },
        )
        profile["cases"] += 1
        profile["completed"] += int(result.get("status") == "completed")
        outcome = result.get("outcome", {})
        if isinstance(outcome, dict):
            profile["deterministic_passes"] += int(outcome.get("deterministic_verification_pass") is True)
            semantic = outcome.get("semantic", {})
            if isinstance(semantic, dict):
                profile["semantic_passes"] += int(semantic.get("verdict") == "pass")
        minimality = result.get("minimality", {})
        if isinstance(minimality, dict):
            profile["files_changed"] += int(minimality.get("files_changed", 0) or 0)
        efficiency = result.get("efficiency", {})
        if isinstance(efficiency, dict):
            profile["model_calls"] += int(efficiency.get("model_calls", 0) or 0)
            cost = efficiency.get("reported_cost")
            if isinstance(cost, (int, float)):
                profile["reported_cost"] = round(float(profile["reported_cost"]) + float(cost), 8)
                profile["reported_cost_known_cases"] += 1
        reliability = result.get("reliability", {})
        if isinstance(reliability, dict):
            profile["provider_failures"] += len(reliability.get("provider_failures", []) or [])
        case = cases.get(str(result.get("case_id")), {})
        for tag in case.get("tags", []) if isinstance(case, dict) else []:
            bucket = tags.setdefault(str(tag), {"runs": 0, "completed": 0})
            bucket["runs"] += 1
            bucket["completed"] += int(result.get("status") == "completed")
    for value in profiles.values():
        if value["reported_cost_known_cases"] == 0:
            value["reported_cost"] = UNKNOWN
    return {"profiles": profiles, "tags": tags}

def render_markdown(results: list[dict[str, object]], aggregate_value: dict[str, object]) -> str:
    lines = [
        "# AutoDev evaluation comparison",
        "",
        "| Case | Profile | Status | Deterministic | Semantic | Files | + | - | Calls | Cost | Comparable |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for result in results:
        outcome = result.get("outcome", {})
        minimal = result.get("minimality", {})
        efficiency = result.get("efficiency", {})
        semantic = outcome.get("semantic", {}) if isinstance(outcome, dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(result.get("case_id", "")),
                    str(result.get("profile", "")),
                    str(result.get("status", "")),
                    str(outcome.get("deterministic_verification_pass", UNKNOWN)) if isinstance(outcome, dict) else UNKNOWN,
                    str(semantic.get("verdict", UNKNOWN)) if isinstance(semantic, dict) else UNKNOWN,
                    str(minimal.get("files_changed", UNKNOWN)) if isinstance(minimal, dict) else UNKNOWN,
                    str(minimal.get("lines_added", UNKNOWN)) if isinstance(minimal, dict) else UNKNOWN,
                    str(minimal.get("lines_deleted", UNKNOWN)) if isinstance(minimal, dict) else UNKNOWN,
                    str(efficiency.get("model_calls", UNKNOWN)) if isinstance(efficiency, dict) else UNKNOWN,
                    str(efficiency.get("reported_cost", UNKNOWN)) if isinstance(efficiency, dict) else UNKNOWN,
                    "yes" if result.get("comparable") else "no",
                ]
            )
            + " |"
        )
        for note in result.get("comparability_notes", []):
            lines.append(f"  - `{result.get('case_id')}` / `{result.get('profile')}`: {note}")
    lines.extend(["", "## Aggregate by profile", ""])
    profiles = aggregate_value.get("profiles", {})
    if isinstance(profiles, dict):
        for name, value in profiles.items():
            lines.extend([f"### {name}", ""])
            if isinstance(value, dict):
                for key in (
                    "cases",
                    "completed",
                    "deterministic_passes",
                    "semantic_passes",
                    "files_changed",
                    "model_calls",
                    "provider_failures",
                    "reported_cost",
                ):
                    lines.append(f"- {key.replace('_', ' ')}: {value.get(key, UNKNOWN)}")
            lines.append("")
    lines.extend(
        [
            "## Interpretation",
            "",
            "No opaque overall score is produced. Compare correctness/semantic outcome first, then minimality, reliability, efficiency, and reported cost. `unknown` means AutoDev had no deterministic source for that metric.",
            "",
        ]
    )
    return "\n".join(lines)

def write_results(output_root: Path, results: list[dict[str, object]], aggregate_value: dict[str, object]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    for result in results:
        case_dir = output_root / str(result["case_id"]) / str(result["profile"])
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "result.json").write_text(
            json.dumps(redact(result), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "results": results,
        "aggregate": aggregate_value,
    }
    (output_root / "aggregate.json").write_text(
        json.dumps(redact(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "comparison.md").write_text(
        render_markdown(results, aggregate_value),
        encoding="utf-8",
    )

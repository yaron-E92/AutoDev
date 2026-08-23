from __future__ import annotations

import fnmatch
import platform
import re
from pathlib import Path
from automation import run_manifest

from automation.evaluation_contract import (
    DEPENDENCY_NAMES,
    UNKNOWN,
)
from automation.evaluation_profiles import (
    fingerprint,
    redact,
)

def parse_diff(diff_text: str) -> dict[str, object]:
    paths: list[str] = []
    additions = 0
    deletions = 0
    new_files: list[str] = []
    current = ""
    for line in diff_text.splitlines():
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if match:
            current = match.group(2)
            if current not in paths:
                paths.append(current)
            continue
        if line.startswith("new file mode ") and current:
            new_files.append(current)
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    dependency_changes = [
        path
        for path in paths
        if Path(path).name in DEPENDENCY_NAMES
        or path.endswith((".csproj", ".fsproj", ".vbproj", ".props", ".targets"))
    ]
    return {
        "paths": paths,
        "files_changed": len(paths),
        "lines_added": additions,
        "lines_deleted": deletions,
        "new_files": sorted(set(new_files)),
        "dependency_manifest_changes": sorted(set(dependency_changes)),
    }

def path_matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)

def stage_record(manifest: dict[str, object], stage: str) -> dict[str, object]:
    stages = manifest.get("stages", {})
    value = stages.get(stage, {}) if isinstance(stages, dict) else {}
    return value if isinstance(value, dict) else {}

def repair_count(manifest: dict[str, object], kind: str) -> int:
    record = stage_record(manifest, "repair-generated")
    values = [record] if record else []
    history = record.get("history", []) if record else []
    if isinstance(history, list):
        values.extend(item for item in history if isinstance(item, dict))
    count = 0
    for value in values:
        details = value.get("details", {})
        if isinstance(details, dict) and str(details.get("kind", "")) == kind:
            count += 1
    return count

def invocation_metrics(manifest: dict[str, object]) -> dict[str, object]:
    raw = manifest.get("invocations", [])
    invocations = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    by_role: dict[str, int] = {}
    failures: list[dict[str, object]] = []
    retries = 0
    prompt_tokens = 0
    output_tokens = 0
    total_tokens = 0
    token_seen = False
    costs: list[float] = []
    compression: list[object] = []
    for item in invocations:
        role = str(item.get("role", "unknown"))
        by_role[role] = by_role.get(role, 0) + 1
        retries += int(item.get("retry_count", 0) or 0)
        if item.get("status") == "failure":
            failures.append(
                {
                    "role": role,
                    "transport": str(item.get("transport") or item.get("provider") or ""),
                    "model": str(item.get("reported_model") or item.get("model") or ""),
                    "classification": str(item.get("failure_classification") or "provider_error"),
                    "status_code": item.get("status_code", UNKNOWN),
                }
            )
        usage = item.get("usage", {})
        if isinstance(usage, dict):
            for key, target in (
                ("prompt_tokens", "prompt"),
                ("input_tokens", "prompt"),
                ("completion_tokens", "output"),
                ("output_tokens", "output"),
                ("total_tokens", "total"),
            ):
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    token_seen = True
                    if target == "prompt":
                        prompt_tokens += int(value)
                    elif target == "output":
                        output_tokens += int(value)
                    else:
                        total_tokens += int(value)
        cost = item.get("reported_cost")
        if isinstance(cost, (int, float)):
            costs.append(float(cost))
        if isinstance(item.get("compression"), dict):
            compression.append(redact(item["compression"]))
    if token_seen and total_tokens == 0:
        total_tokens = prompt_tokens + output_tokens
    return {
        "model_calls": len(invocations),
        "model_calls_by_role": by_role,
        "malformed_or_provider_retries": retries,
        "provider_failures": failures,
        "prompt_tokens": prompt_tokens if token_seen else UNKNOWN,
        "output_tokens": output_tokens if token_seen else UNKNOWN,
        "total_tokens": total_tokens if token_seen else UNKNOWN,
        "reported_cost": round(sum(costs), 8) if costs else UNKNOWN,
        "compression": compression or UNKNOWN,
    }

def stage_timing(manifest: dict[str, object], diagnostics: dict[str, object]) -> dict[str, object]:
    value = diagnostics.get("stage_wall_time_ms", {}) if isinstance(diagnostics, dict) else {}
    result: dict[str, object] = {}
    if isinstance(value, dict):
        for stage, raw in value.items():
            if isinstance(raw, list):
                result[str(stage)] = sum(int(item) for item in raw if isinstance(item, (int, float)))
            elif isinstance(raw, (int, float)):
                result[str(stage)] = int(raw)
    if result:
        return result
    stages = manifest.get("stages", {})
    if isinstance(stages, dict):
        for stage, raw in stages.items():
            if not isinstance(raw, dict):
                continue
            details = raw.get("details", {})
            if isinstance(details, dict) and isinstance(details.get("elapsed_ms"), (int, float)):
                result[str(stage)] = int(details["elapsed_ms"])
    return result

def semantic_metrics(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"verdict": UNKNOWN, "requirements_met": UNKNOWN, "requirements_total": UNKNOWN}
    requirements = value.get("requirements", [])
    reqs = [item for item in requirements if isinstance(item, dict)] if isinstance(requirements, list) else []
    return {
        "verdict": str(value.get("verdict", UNKNOWN)),
        "requirements_met": sum(1 for item in reqs if item.get("status") == "met"),
        "requirements_total": len(reqs),
    }

def score_record(
    case: dict[str, object],
    profile: dict[str, object],
    *,
    manifest: dict[str, object],
    semantic: object,
    diff_text: str,
    diagnostics: dict[str, object],
    replay_meta: dict[str, object],
    mode: str,
) -> dict[str, object]:
    expected = case.get("expected", {})
    assert isinstance(expected, dict)
    diff = parse_diff(diff_text)
    paths = [str(item) for item in diff["paths"]]
    expected_patterns = [str(item) for item in expected.get("changed_paths", [])]
    forbidden_patterns = [str(item) for item in expected.get("forbidden_paths", [])]
    expected_missing = [
        pattern for pattern in expected_patterns if not any(path_matches(path, [pattern]) for path in paths)
    ]
    forbidden_touched = [path for path in paths if path_matches(path, forbidden_patterns)]
    no_change_expected = bool(expected.get("no_change", False))
    semantic_result = semantic_metrics(semantic)
    invocations = invocation_metrics(manifest)
    deterministic_pass = run_manifest.stage_completed(manifest, "deterministic-verified")
    failure = manifest.get("failure", {})
    failure = failure if isinstance(failure, dict) else {}
    roles = profile.get("provider_summary", {}).get("roles", {})
    free_profile = any(
        isinstance(item, dict) and bool(item.get("free_only"))
        for item in roles.values()
    ) if isinstance(roles, dict) else False
    free_unavailable = free_profile and any(
        item.get("classification") in {
            "provider_unavailable",
            "rate_limited",
            "quota_exhausted",
            "free_model_unavailable",
            "http_error",
        }
        or item.get("status_code") in {402, 404, 429}
        for item in invocations["provider_failures"]
    )
    status = "completed"
    if free_unavailable:
        status = "unavailable/provider-failed"
    elif failure:
        status = "provider-failed" if invocations["provider_failures"] else "failed"
    comparability_reasons: list[str] = []
    captured_profile = str(replay_meta.get("profile_fingerprint", ""))
    if captured_profile and captured_profile != str(profile.get("fingerprint", "")):
        comparability_reasons.append("captured provider/profile fingerprint differs from the selected profile")
    if replay_meta.get("case_version") not in (None, case.get("version", 1)):
        comparability_reasons.append("captured case version differs")
    target = manifest.get("target", {})
    target = target if isinstance(target, dict) else {}
    expected_base = str(case.get("base_commit", "") or "")
    if expected_base and str(target.get("base_sha", "")) != expected_base:
        comparability_reasons.append("target base SHA differs")
    deterministic_repairs = repair_count(manifest, "deterministic")
    semantic_repairs = repair_count(manifest, "semantic")
    ci_repairs = repair_count(manifest, "ci")
    return {
        "case_id": str(case["id"]),
        "case_version": case.get("version", 1),
        "profile": str(profile["name"]),
        "mode": mode,
        "status": status,
        "comparable": not comparability_reasons,
        "comparability_notes": comparability_reasons,
        "outcome": {
            "patch_applies_cleanly": replay_meta.get("patch_applies_cleanly", UNKNOWN),
            "deterministic_verification_pass": deterministic_pass,
            "semantic": semantic_result,
            "expected_paths_missing": expected_missing,
            "forbidden_paths_touched": forbidden_touched,
            "no_change_expected": no_change_expected,
            "no_change_correct": (not paths) if no_change_expected else bool(paths),
            "human_rating": expected.get("human_rating", UNKNOWN),
        },
        "minimality": {
            **diff,
            "new_abstractions": replay_meta.get("new_abstractions", UNKNOWN),
            "unrelated_cleanup_findings": len(forbidden_touched),
        },
        "reliability": {
            "first_pass_success": deterministic_pass and deterministic_repairs == 0 and not failure,
            "deterministic_repair_count": deterministic_repairs,
            "semantic_repair_count": semantic_repairs,
            "ci_repair_count": ci_repairs,
            "malformed_or_provider_retries": invocations["malformed_or_provider_retries"],
            "provider_failures": invocations["provider_failures"],
            "rate_or_quota_failures": sum(
                1
                for item in invocations["provider_failures"]
                if item.get("status_code") in {402, 429}
                or item.get("classification") in {"rate_limited", "quota_exhausted"}
            ),
            "fallback_attempts": replay_meta.get("fallback_attempts", UNKNOWN),
            "blocked": str(failure.get("classification", "")) == "blocked"
            or semantic_result["verdict"] == "blocked",
            "resume_count": replay_meta.get("resume_count", UNKNOWN),
            "free_model_unavailable_without_paid_substitution": free_unavailable,
        },
        "efficiency": {
            "stage_wall_time_ms": stage_timing(manifest, diagnostics),
            "model_calls": invocations["model_calls"],
            "model_calls_by_role": invocations["model_calls_by_role"],
            "prompt_tokens": invocations["prompt_tokens"],
            "output_tokens": invocations["output_tokens"],
            "total_tokens": invocations["total_tokens"],
            "headroom_compression": invocations["compression"],
            "reported_cost": invocations["reported_cost"],
            "estimated_cost": UNKNOWN,
        },
        "reproducibility": {
            "autodev_commit": replay_meta.get("autodev_commit", UNKNOWN),
            "target_commit": str(target.get("base_sha", "")) or UNKNOWN,
            "provider_profile": str(profile.get("provider_config", "")),
            "provider_summary": profile.get("provider_summary", {}),
            "profile_fingerprint": str(profile.get("fingerprint", "")),
            "prompt_policy": manifest.get(
                "prompt_policy",
                profile.get("provider_summary", {}).get("prompt_policy", {}),
            ),
            "headroom": profile.get("provider_summary", {}).get("headroom", {}),
            "runner_manifest_schema": manifest.get("schema_version", UNKNOWN),
            "os": replay_meta.get("os", platform.platform()),
            "tool_versions": redact(replay_meta.get("tool_versions", {})),
            "random_settings": redact(replay_meta.get("random_settings", UNKNOWN)),
            "output_source": mode,
            "case_input_hash": fingerprint(
                {
                    "id": case["id"],
                    "version": case.get("version", 1),
                    "issue_text": case.get("issue_text", ""),
                    "base_commit": case.get("base_commit", ""),
                    "expected": expected,
                }
            ),
        },
    }

def unavailable_result(case: dict[str, object], profile: dict[str, object], reason: str) -> dict[str, object]:
    return {
        "case_id": str(case["id"]),
        "case_version": case.get("version", 1),
        "profile": str(profile["name"]),
        "mode": "replay",
        "status": "unavailable",
        "comparable": False,
        "comparability_notes": [reason],
        "outcome": {},
        "minimality": {},
        "reliability": {},
        "efficiency": {"reported_cost": UNKNOWN},
        "reproducibility": {
            "provider_profile": str(profile.get("provider_config", "")),
            "profile_fingerprint": str(profile.get("fingerprint", "")),
        },
    }

def estimate_model_calls(profile: dict[str, object]) -> int:
    summary = profile.get("provider_summary", {})
    roles = summary.get("roles", {}) if isinstance(summary, dict) else {}
    base = sum(1 for role in ("reader", "synthesizer", "planner", "implementer", "verifier") if role in roles)
    semantic = summary.get("semantic_verification", {}) if isinstance(summary, dict) else {}
    schema_retries = int(semantic.get("max_schema_retries", 0) or 0) if isinstance(semantic, dict) else 0
    semantic_repairs = int(semantic.get("max_repair_attempts", 0) or 0) if isinstance(semantic, dict) else 0
    evaluation = profile.get("evaluation", {})
    max_fix = int(evaluation.get("max_fix_attempts", 2) or 0) if isinstance(evaluation, dict) else 2
    return base + max_fix + schema_retries + semantic_repairs * 2

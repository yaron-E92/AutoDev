from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from automation import run_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = REPO_ROOT / "benchmarks" / "eval" / "cases.json"
DEFAULT_PROFILES = REPO_ROOT / "benchmarks" / "eval" / "profiles.json"
DEFAULT_RESULTS_ROOT = REPO_ROOT / ".benchmark-results"
SCHEMA_VERSION = 1
UNKNOWN = "unknown"
DEPENDENCY_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "requirements.txt",
    "pyproject.toml",
    "poetry.lock",
    "Pipfile",
    "Pipfile.lock",
    "Directory.Packages.props",
    "packages.lock.json",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
}


class EvalError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalError(f"evaluation file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvalError(f"evaluation JSON is invalid: {path}") from exc


def load_cases(path: Path) -> dict[str, dict[str, object]]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise EvalError(f"unsupported evaluation case schema in {path}")
    raw = value.get("cases")
    if not isinstance(raw, list) or not raw:
        raise EvalError("evaluation cases must be a non-empty array")
    cases: dict[str, dict[str, object]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise EvalError("each evaluation case must be an object")
        case_id = str(item.get("id", "")).strip()
        if not case_id or case_id in cases:
            raise EvalError(f"evaluation case id is missing or duplicated: {case_id or '<empty>'}")
        if not str(item.get("issue_text", "")).strip():
            raise EvalError(f"evaluation case {case_id} has no issue_text")
        source = item.get("source")
        if not isinstance(source, dict) or source.get("kind") not in {"replay", "fixture", "public"}:
            raise EvalError(f"evaluation case {case_id} has an invalid source")
        if not isinstance(item.get("expected"), dict):
            raise EvalError(f"evaluation case {case_id} has no expected object")
        cases[case_id] = item
    return cases


def load_profiles(path: Path, *, repo_root: Path = REPO_ROOT) -> dict[str, dict[str, object]]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise EvalError(f"unsupported evaluation profile schema in {path}")
    raw = value.get("profiles")
    if not isinstance(raw, dict) or not raw:
        raise EvalError("evaluation profiles must be a non-empty object")
    profiles: dict[str, dict[str, object]] = {}
    for name, item in raw.items():
        if not isinstance(item, dict):
            raise EvalError(f"evaluation profile {name} must be an object")
        provider_value = str(item.get("provider_config", "")).strip()
        if not provider_value:
            raise EvalError(f"evaluation profile {name} has no provider_config")
        provider_path = (repo_root / provider_value).resolve()
        provider = read_json(provider_path)
        if not isinstance(provider, dict):
            raise EvalError(f"evaluation profile {name} provider config must be an object")
        summary = safe_provider_summary(provider)
        ensure_free_route_safety(str(name), summary)
        profiles[str(name)] = {
            **item,
            "name": str(name),
            "provider_path": str(provider_path),
            "provider_summary": summary,
            "fingerprint": fingerprint(
                {
                    "provider_config": provider_value,
                    "provider_summary": summary,
                    "evaluation": item.get("evaluation", {}),
                }
            ),
        }
    return profiles


def safe_provider_summary(config: dict[str, object]) -> dict[str, object]:
    roles_value = config.get("roles")
    roles = roles_value if isinstance(roles_value, dict) else {
        role: config.get(role, {})
        for role in ("reader", "coder")
        if isinstance(config.get(role), dict)
    }
    safe_roles: dict[str, object] = {}
    for role, raw in roles.items():
        if not isinstance(raw, dict):
            continue
        safe_roles[str(role)] = {
            "transport": str(raw.get("transport") or raw.get("provider") or ""),
            "model": str(raw.get("model") or ""),
            "profile_name": str(raw.get("profile_name") or ""),
            "endpoint": sanitized_url(raw.get("base_url", "")),
            "timeout_seconds": raw.get("timeout_seconds", UNKNOWN),
            "free_only": bool(raw.get("free_only", False)),
            "api_key_env": str(raw.get("api_key_env") or ""),
            "fallbacks": safe_fallbacks(raw),
        }
    return {
        "name": str(config.get("name") or ""),
        "version": config.get("version", UNKNOWN),
        "roles": safe_roles,
        "prompt_policy": redact(config.get("prompt_policy", {})),
        "semantic_verification": redact(config.get("semantic_verification", {})),
        "headroom": safe_headroom(config.get("headroom", {})),
    }


def safe_fallbacks(role_config: dict[str, object]) -> list[str]:
    value = role_config.get("fallbacks", role_config.get("fallback_models", []))
    return [str(item) for item in value] if isinstance(value, list) else []


def safe_headroom(value: object) -> object:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in {"enabled", "mode", "output_shaping", "fail_open", "roles", "version"}
    }


def redact(value: object, key: str = "") -> object:
    lowered = key.casefold().replace("-", "_")
    if any(token in lowered for token in ("api_key", "authorization", "password", "secret", "token", "cookie")):
        return "<redacted>"
    if lowered == "headers":
        return {str(item_key): "<redacted>" for item_key in value} if isinstance(value, dict) else "<redacted>"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    return value


def sanitized_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return "configured"
    hostname = parts.hostname or ""
    if parts.port is not None:
        hostname += f":{parts.port}"
    return urlunsplit((parts.scheme, hostname, parts.path, "", ""))


def ensure_free_route_safety(profile_name: str, summary: dict[str, object]) -> None:
    roles = summary.get("roles", {})
    if not isinstance(roles, dict):
        return
    for role, raw in roles.items():
        if not isinstance(raw, dict):
            continue
        model = str(raw.get("model", ""))
        endpoint = str(raw.get("endpoint", "")).casefold()
        if "openrouter.ai" not in endpoint or not (model.endswith(":free") or raw.get("free_only")):
            continue
        if not model.endswith(":free") or not bool(raw.get("free_only")):
            raise EvalError(
                f"profile {profile_name} role {role} is an OpenRouter free comparison without both :free and free_only=true"
            )
        paid = [item for item in raw.get("fallbacks", []) if not str(item).endswith(":free")]
        if paid:
            raise EvalError(f"profile {profile_name} role {role} permits a paid fallback: {', '.join(paid)}")


def fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def selected_cases(
    all_cases: dict[str, dict[str, object]],
    requested: list[str],
    tags: list[str],
) -> list[dict[str, object]]:
    values = [all_cases[name] for name in requested] if requested else list(all_cases.values())
    if tags:
        wanted = set(tags)
        values = [case for case in values if wanted.intersection(str(tag) for tag in case.get("tags", []))]
    return values


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


def load_replay(case: dict[str, object], profile: dict[str, object], *, cases_path: Path) -> dict[str, object]:
    source = case["source"]
    assert isinstance(source, dict)
    replay_file = str(source.get("replay_file", "")).strip()
    if not replay_file:
        return unavailable_result(case, profile, "case has no replay file")
    path = (cases_path.parent / replay_file).resolve()
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise EvalError(f"invalid replay fixture: {path}")
    profiles = value.get("profiles", {})
    record = profiles.get(profile["name"]) if isinstance(profiles, dict) else None
    if not isinstance(record, dict):
        return unavailable_result(case, profile, "no replay recorded for this profile")
    manifest = record.get("run_manifest", {})
    if not isinstance(manifest, dict):
        raise EvalError(f"replay {path} for {profile['name']} has no run_manifest")
    run_manifest.validate_manifest(manifest)
    diagnostics = record.get("run_diagnostics", {})
    return score_record(
        case,
        profile,
        manifest=manifest,
        semantic=record.get("semantic_result", {}),
        diff_text=str(record.get("diff", "")),
        diagnostics=diagnostics if isinstance(diagnostics, dict) else {},
        replay_meta=record,
        mode="replay",
    )


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


def live_plan(case: dict[str, object], profile: dict[str, object]) -> dict[str, object]:
    source = case.get("source", {})
    live = source.get("live", {}) if isinstance(source, dict) else {}
    if not isinstance(live, dict) or not live:
        raise EvalError(f"case {case['id']} has no live runner target")
    repo = str(live.get("repo", "")).strip()
    repo_env = str(live.get("repo_env", "")).strip()
    if not repo and repo_env:
        repo = os.environ.get(repo_env, "").strip()
    if not repo:
        raise EvalError(f"case {case['id']} requires local repo path or {repo_env or 'repo_env'}")
    github_repo = str(live.get("github_repo", "")).strip()
    issue = int(live.get("issue", 0) or 0)
    if not github_repo or issue <= 0:
        raise EvalError(f"case {case['id']} live target requires github_repo and issue")
    return {
        "repo": str(Path(repo).expanduser().resolve()),
        "github_repo": github_repo,
        "issue": issue,
        "provider_config": str(profile["provider_path"]),
        "roles": profile["provider_summary"]["roles"],
    }


def run_live_case(
    case: dict[str, object],
    profile: dict[str, object],
    *,
    output_dir: Path,
    timeout_seconds: int,
    sandbox_pr: bool,
) -> dict[str, object]:
    plan = live_plan(case, profile)
    for role, value in plan["roles"].items():
        if isinstance(value, dict) and "REPLACE_WITH" in str(value.get("model", "")):
            raise EvalError(f"profile {profile['name']} role {role} still contains a placeholder model")
    run_dir = output_dir / "run"
    command = [
        sys.executable,
        "-m",
        "automation.run_real_issue",
        "--repo",
        str(plan["repo"]),
        "--github-repo",
        str(plan["github_repo"]),
        "--issue",
        str(plan["issue"]),
        "--mode",
        "pr" if sandbox_pr else "implement",
        "--provider-config",
        str(plan["provider_config"]),
        "--out",
        str(run_dir),
        "--max-fix-attempts",
        str((profile.get("evaluation", {}) or {}).get("max_fix_attempts", 2)),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    manifest_path = run_dir / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return unavailable_result(case, profile, "runner did not produce run-manifest.json") | {
            "mode": "live",
            "status": "provider-failed" if completed.returncode else "failed",
        }
    manifest = run_manifest.load_manifest(manifest_path)
    result = score_record(
        case,
        profile,
        manifest=manifest,
        semantic=read_optional_json(run_dir / "verification" / "final-verdict.json"),
        diff_text=git_diff(Path(str(plan["repo"]))),
        diagnostics=read_optional_json(run_dir / "run-diagnostics.json"),
        replay_meta={
            "autodev_commit": git_rev_parse(REPO_ROOT),
            "case_version": case.get("version", 1),
            "profile_fingerprint": profile["fingerprint"],
            "patch_applies_cleanly": completed.returncode == 0,
            "os": platform.platform(),
        },
        mode="live",
    )
    if completed.returncode != 0 and result["status"] == "completed":
        result["status"] = "failed"
    return result


def read_optional_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def git_diff(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else ""


def git_rev_parse(repo: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else UNKNOWN


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay-first AutoDev workflow evaluation harness.")
    parser.add_argument("--cases-file", default=str(DEFAULT_CASES))
    parser.add_argument("--profiles-file", default=str(DEFAULT_PROFILES))
    parser.add_argument("--profile", action="append", dest="profiles", required=True)
    parser.add_argument("--case", action="append", dest="cases", default=[])
    parser.add_argument("--tag", action="append", dest="tags", default=[])
    parser.add_argument("--out")
    parser.add_argument("--live", action="store_true", help="Permit normal AutoDev runner/model execution.")
    parser.add_argument("--apply", action="store_true", help="Permit target working-tree edits for live runs.")
    parser.add_argument("--sandbox-pr", action="store_true", help="Permit PR mode for an explicitly sandboxed live target.")
    parser.add_argument("--max-cases", type=int, default=10)
    parser.add_argument("--max-model-calls", type=int, default=50)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--max-reported-cost", type=float)
    return parser


def validate_budgets(
    args: argparse.Namespace,
    cases: list[dict[str, object]],
    profiles: list[dict[str, object]],
) -> None:
    if len(cases) > args.max_cases:
        raise EvalError(f"selected {len(cases)} cases exceeds --max-cases {args.max_cases}")
    planned_calls = sum(estimate_model_calls(profile) for profile in profiles) * len(cases)
    if args.live and planned_calls > args.max_model_calls:
        raise EvalError(
            f"conservative planned model-call bound {planned_calls} exceeds --max-model-calls {args.max_model_calls}"
        )
    if args.timeout_seconds <= 0:
        raise EvalError("--timeout-seconds must be greater than zero")
    if args.max_reported_cost is not None and args.max_reported_cost < 0:
        raise EvalError("--max-reported-cost must be zero or greater")
    if args.sandbox_pr and not args.live:
        raise EvalError("--sandbox-pr requires --live")
    if args.live and not args.apply:
        raise EvalError("--live requires --apply because normal implement mode verifies applied working-tree changes")


def print_live_plan(cases: list[dict[str, object]], profiles: list[dict[str, object]], *, sandbox_pr: bool) -> None:
    print("Planned live AutoDev evaluation:")
    for profile in profiles:
        print(f"- profile {profile['name']}: {profile['provider_config']}")
        roles = profile["provider_summary"].get("roles", {})
        if isinstance(roles, dict):
            for role, value in roles.items():
                if not isinstance(value, dict):
                    continue
                print(
                    f"    {role}: {value.get('transport', '')} {value.get('model', '')} "
                    f"endpoint={value.get('endpoint', '') or 'command/local'} "
                    f"fallbacks={value.get('fallbacks', []) or 'none'}"
                )
    print("Cases: " + ", ".join(str(case["id"]) for case in cases))
    print("PR creation: explicit sandbox enabled" if sandbox_pr else "PR creation: disabled")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cases_path = Path(args.cases_file).expanduser().resolve()
        profiles_path = Path(args.profiles_file).expanduser().resolve()
        all_cases = load_cases(cases_path)
        all_profiles = load_profiles(profiles_path)
        missing_cases = sorted(set(args.cases) - set(all_cases))
        missing_profiles = sorted(set(args.profiles) - set(all_profiles))
        if missing_cases:
            raise EvalError("unknown evaluation case(s): " + ", ".join(missing_cases))
        if missing_profiles:
            raise EvalError("unknown evaluation profile(s): " + ", ".join(missing_profiles))
        cases = selected_cases(all_cases, args.cases, args.tags)
        profiles = [all_profiles[name] for name in args.profiles]
        if len(profiles) < 2:
            raise EvalError("compare at least two --profile values")
        if not cases:
            raise EvalError("no evaluation cases selected")
        validate_budgets(args, cases, profiles)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_root = Path(args.out).expanduser().resolve() if args.out else DEFAULT_RESULTS_ROOT / timestamp
        results: list[dict[str, object]] = []
        if args.live:
            print_live_plan(cases, profiles, sandbox_pr=args.sandbox_pr)
        cumulative_reported_cost = 0.0
        for case in cases:
            for profile in profiles:
                if args.max_reported_cost is not None and cumulative_reported_cost >= args.max_reported_cost:
                    raise EvalError(
                        f"reported-cost ceiling {args.max_reported_cost} reached before the next live run"
                    )
                result = (
                    run_live_case(
                        case,
                        profile,
                        output_dir=output_root / str(case["id"]) / str(profile["name"]),
                        timeout_seconds=args.timeout_seconds,
                        sandbox_pr=args.sandbox_pr,
                    )
                    if args.live
                    else load_replay(case, profile, cases_path=cases_path)
                )
                results.append(result)
                efficiency = result.get("efficiency", {})
                cost = efficiency.get("reported_cost") if isinstance(efficiency, dict) else None
                if isinstance(cost, (int, float)):
                    cumulative_reported_cost += float(cost)
        aggregate_value = aggregate(results, all_cases)
        write_results(output_root, results, aggregate_value)
        print(output_root)
        return 0
    except (EvalError, run_manifest.ManifestError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

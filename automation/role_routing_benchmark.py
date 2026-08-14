from __future__ import annotations

import json
from pathlib import Path

from automation import eval_harness_core as core
from automation import privacy
from automation.model_providers import model_config_from_values


UNKNOWN = core.UNKNOWN
ROUTING_ROLES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
REQUIRED_CANDIDATE_ROLES = ("planner", "implementer", "fixer", "verifier")
CANDIDATE_CLASSES = {
    "local",
    "free-cloud",
    "cloud-plan-dependent",
    "frontier-baseline",
    "cloud",
    "unknown",
}
CLASS_RANK = {
    "local": 0,
    "free-cloud": 1,
    "cloud-plan-dependent": 2,
    "frontier-baseline": 3,
    "cloud": 4,
    "unknown": 5,
}


def load_provider_facts(path: Path) -> dict[str, object]:
    value = core.read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise core.EvalError(f"unsupported provider-facts schema in {path}")
    facts = value.get("facts")
    if not isinstance(facts, dict):
        raise core.EvalError(f"provider facts must contain an object: {path}")
    for key, raw in facts.items():
        if not isinstance(raw, dict):
            raise core.EvalError(f"provider fact {key} must be an object")
        candidate_class = str(raw.get("candidate_class", "unknown"))
        if candidate_class not in CANDIDATE_CLASSES:
            raise core.EvalError(f"provider fact {key} has unknown candidate_class {candidate_class}")
        sources = raw.get("source", [])
        if not isinstance(sources, list):
            raise core.EvalError(f"provider fact {key} source must be an array")
    return value


def _fact_key(
    profile_name: str,
    raw: dict[str, object],
    safe: dict[str, object],
) -> str:
    profile = profile_name.casefold()
    endpoint = str(safe.get("endpoint", "")).casefold()
    model = str(safe.get("model", "")).casefold()
    command = str(raw.get("command", "")).casefold()

    if "api.groq.com" in endpoint:
        return "groq/openai-gpt-oss-20b" if model == "openai/gpt-oss-20b" else "groq/generic"
    if "openrouter.ai" in endpoint:
        return "openrouter/free" if bool(safe.get("free_only")) or model.endswith(":free") else "openrouter/generic"
    if profile == "local-ollama":
        return "ollama/local"
    if "ollama-cloud" in profile or ":cloud" in model or ":cloud" in command:
        return "ollama/cloud"
    if "ollama " in f" {command} " or command.startswith("ollama "):
        return "ollama/local"
    if profile == "legacy-command" or command.startswith("codex ") or " codex " in f" {command} ":
        return "openai/codex-baseline"
    return "unknown"


def _fact(facts: dict[str, object], key: str) -> dict[str, object]:
    values = facts.get("facts", {})
    if not isinstance(values, dict):
        return {}
    raw = values.get(key, values.get("unknown", {}))
    return dict(raw) if isinstance(raw, dict) else {}


def _config_for_preview(role: str, raw: dict[str, object], fact_key: str) -> object:
    values = dict(raw)
    if fact_key in {"ollama/local", "ollama/cloud"} and not str(values.get("command", "")).strip():
        model = str(values.get("model", "")).strip()
        values["command"] = f"ollama run {model}"
    return model_config_from_values(role, values)


def _privacy_preview(config, *, role: str, policy_name: str) -> dict[str, object]:
    policy = privacy.PrivacyPolicy(
        profile=policy_name,
        consent_mode="deny",
        source="evaluation-preview",
    )
    provider_id = privacy._provider_id(config)
    model = str(config.model or "")
    route = f"{provider_id}/{model}" if model else provider_id

    if policy.local_only and provider_id != "local":
        return privacy.PrivacyDecision(
            "BLOCK",
            role,
            route,
            provider_id,
            model,
            privacy._scope(provider_id),
            reason="local-only forbids cloud processing",
        ).safe_metadata()

    if provider_id == "openrouter":
        controls = [
            f"provider.{key}={json.dumps(value)}"
            for key, value in privacy._openrouter_controls(policy).items()
        ]
        decision = privacy.PrivacyDecision(
            "CONSENT_REQUIRED",
            role,
            route,
            provider_id,
            model,
            "routed-cloud",
            training="unknown",
            retention="unknown",
            policy_source=(
                "https://openrouter.ai/docs/guides/routing/provider-selection; "
                "https://openrouter.ai/docs/guides/privacy/data-collection"
            ),
            enforcement_state="request-control-available-not-executed",
            controls=controls,
            reason=(
                "runtime routing controls can be injected by AutoDev, but a static benchmark preview "
                "cannot verify repository/account attestations for OpenRouter-owned data settings"
            ),
        )
        return decision.safe_metadata()

    training, retention, duration, source = privacy._classify(provider_id)
    decision = privacy.PrivacyDecision(
        "ALLOW",
        role,
        route,
        provider_id,
        model,
        privacy._scope(provider_id),
        training,
        retention,
        duration,
        policy_source=source,
        enforcement_state=(
            "verified-effective" if provider_id == "local" else "enforced-by-provider-contract"
        ),
    )
    if not privacy._satisfies(policy, decision):
        decision.outcome = "CONSENT_REQUIRED"
        decision.reason = privacy._gap(policy, decision)
    return decision.safe_metadata()


def _role_metadata(
    profile_name: str,
    role: str,
    raw: dict[str, object],
    safe: dict[str, object],
    facts: dict[str, object],
) -> dict[str, object]:
    key = _fact_key(profile_name, raw, safe)
    fact = _fact(facts, key)
    candidate_class = str(fact.get("candidate_class", "unknown"))
    try:
        config = _config_for_preview(role, raw, key)
        privacy_matrix = {
            name: _privacy_preview(config, role=role, policy_name=name)
            for name in ("no-training", "strict-confidential", "local-only")
        }
    except Exception as exc:
        privacy_matrix = {
            name: {
                "outcome": "UNKNOWN",
                "reason": f"could not statically classify route: {type(exc).__name__}",
            }
            for name in ("no-training", "strict-confidential", "local-only")
        }
    return {
        "candidate_id": f"{key}:{safe.get('model', '')}",
        "candidate_class": candidate_class,
        "fact_key": key,
        "request_limits": fact.get("request_limits", {"status": UNKNOWN}),
        "tool_contract": fact.get("tool_contract", UNKNOWN),
        "availability": fact.get("availability", UNKNOWN),
        "source": fact.get("source", []),
        "checked_at": fact.get("checked_at", facts.get("checked_at", UNKNOWN)),
        "privacy": privacy_matrix,
    }


def enrich_profiles(
    profiles: dict[str, dict[str, object]],
    *,
    repo_root: Path,
) -> dict[str, dict[str, object]]:
    facts = load_provider_facts(repo_root / "benchmarks" / "eval" / "provider-facts.json")
    for profile in profiles.values():
        raw = core.read_json(Path(str(profile["provider_path"])))
        raw_roles = raw.get("roles", {}) if isinstance(raw, dict) else {}
        safe_summary = profile.get("provider_summary", {})
        safe_roles = safe_summary.get("roles", {}) if isinstance(safe_summary, dict) else {}
        benchmark_roles: dict[str, object] = {}
        if isinstance(raw_roles, dict) and isinstance(safe_roles, dict):
            for role, raw_role in raw_roles.items():
                safe_role = safe_roles.get(role, {})
                if isinstance(raw_role, dict) and isinstance(safe_role, dict):
                    benchmark_roles[str(role)] = _role_metadata(
                        str(profile.get("name", "")),
                        str(role),
                        raw_role,
                        safe_role,
                        facts,
                    )
        if isinstance(safe_summary, dict):
            safe_summary["benchmark"] = {
                "facts_checked_at": facts.get("checked_at", UNKNOWN),
                "roles": benchmark_roles,
            }
    return profiles


def _workflow_wall_time(result: dict[str, object]) -> int | None:
    efficiency = result.get("efficiency", {})
    stages = efficiency.get("stage_wall_time_ms", {}) if isinstance(efficiency, dict) else {}
    if not isinstance(stages, dict):
        return None
    values = [int(value) for value in stages.values() if isinstance(value, (int, float))]
    return sum(values) if values else None


def _candidate_bucket(
    groups: dict[str, dict[str, dict[str, object]]],
    role: str,
    candidate_id: str,
    metadata: dict[str, object],
    safe_role: dict[str, object],
) -> dict[str, object]:
    return groups.setdefault(role, {}).setdefault(
        candidate_id,
        {
            "candidate_id": candidate_id,
            "candidate_class": metadata.get("candidate_class", "unknown"),
            "model": safe_role.get("model", ""),
            "transport": safe_role.get("transport", ""),
            "endpoint": safe_role.get("endpoint", ""),
            "profiles": [],
            "configured_runs": 0,
            "observed_runs": 0,
            "completed_workflows": 0,
            "deterministic_passes": 0,
            "semantic_passes": 0,
            "provider_failures": 0,
            "downstream_repairs": 0,
            "model_calls": 0,
            "workflow_tokens_known": 0,
            "workflow_total_tokens": 0,
            "workflow_wall_time_known": 0,
            "workflow_wall_time_ms": 0,
            "request_limits": metadata.get("request_limits", {"status": UNKNOWN}),
            "tool_contract": metadata.get("tool_contract", UNKNOWN),
            "availability": metadata.get("availability", UNKNOWN),
            "privacy": metadata.get("privacy", {}),
            "source": metadata.get("source", []),
            "checked_at": metadata.get("checked_at", UNKNOWN),
        },
    )


def add_result(
    groups: dict[str, dict[str, dict[str, object]]],
    result: dict[str, object],
) -> None:
    reproducibility = result.get("reproducibility", {})
    provider = reproducibility.get("provider_summary", {}) if isinstance(reproducibility, dict) else {}
    roles = provider.get("roles", {}) if isinstance(provider, dict) else {}
    benchmark = provider.get("benchmark", {}) if isinstance(provider, dict) else {}
    benchmark_roles = benchmark.get("roles", {}) if isinstance(benchmark, dict) else {}
    efficiency = result.get("efficiency", {})
    calls_by_role = efficiency.get("model_calls_by_role", {}) if isinstance(efficiency, dict) else {}
    reliability = result.get("reliability", {})
    failures = reliability.get("provider_failures", []) if isinstance(reliability, dict) else []
    outcome = result.get("outcome", {})
    semantic = outcome.get("semantic", {}) if isinstance(outcome, dict) else {}
    repairs = 0
    if isinstance(reliability, dict):
        repairs = sum(
            int(reliability.get(key, 0) or 0)
            for key in ("deterministic_repair_count", "semantic_repair_count", "ci_repair_count")
        )

    if not isinstance(roles, dict) or not isinstance(benchmark_roles, dict):
        return
    for role, safe_role in roles.items():
        if role not in ROUTING_ROLES or not isinstance(safe_role, dict):
            continue
        metadata = benchmark_roles.get(role, {})
        if not isinstance(metadata, dict):
            continue
        candidate_id = str(metadata.get("candidate_id", "")) or f"unknown:{safe_role.get('model', '')}"
        bucket = _candidate_bucket(groups, role, candidate_id, metadata, safe_role)
        bucket["configured_runs"] = int(bucket["configured_runs"]) + 1
        profiles = bucket["profiles"]
        if isinstance(profiles, list):
            profile = str(result.get("profile", ""))
            if profile and profile not in profiles:
                profiles.append(profile)

        role_calls = int(calls_by_role.get(role, 0) or 0) if isinstance(calls_by_role, dict) else 0
        if role_calls <= 0:
            continue
        bucket["observed_runs"] = int(bucket["observed_runs"]) + 1
        bucket["model_calls"] = int(bucket["model_calls"]) + role_calls
        bucket["completed_workflows"] = int(bucket["completed_workflows"]) + int(result.get("status") == "completed")
        if isinstance(outcome, dict):
            bucket["deterministic_passes"] = int(bucket["deterministic_passes"]) + int(
                outcome.get("deterministic_verification_pass") is True
            )
        if isinstance(semantic, dict):
            bucket["semantic_passes"] = int(bucket["semantic_passes"]) + int(
                semantic.get("verdict") == "pass"
            )
        if isinstance(failures, list):
            bucket["provider_failures"] = int(bucket["provider_failures"]) + sum(
                1
                for failure in failures
                if isinstance(failure, dict) and failure.get("role") == role
            )
        bucket["downstream_repairs"] = int(bucket["downstream_repairs"]) + repairs
        total_tokens = efficiency.get("total_tokens") if isinstance(efficiency, dict) else None
        if isinstance(total_tokens, (int, float)):
            bucket["workflow_tokens_known"] = int(bucket["workflow_tokens_known"]) + 1
            bucket["workflow_total_tokens"] = int(bucket["workflow_total_tokens"]) + int(total_tokens)
        wall = _workflow_wall_time(result)
        if wall is not None:
            bucket["workflow_wall_time_known"] = int(bucket["workflow_wall_time_known"]) + 1
            bucket["workflow_wall_time_ms"] = int(bucket["workflow_wall_time_ms"]) + wall


def candidate_rates(bucket: dict[str, object]) -> dict[str, object]:
    observed = int(bucket.get("observed_runs", 0) or 0)
    if observed <= 0:
        return {
            "observed": False,
            "completion_rate": UNKNOWN,
            "deterministic_pass_rate": UNKNOWN,
            "semantic_pass_rate": UNKNOWN,
            "avg_downstream_repairs": UNKNOWN,
            "avg_workflow_tokens": UNKNOWN,
            "avg_workflow_wall_time_ms": UNKNOWN,
        }
    token_known = int(bucket.get("workflow_tokens_known", 0) or 0)
    wall_known = int(bucket.get("workflow_wall_time_known", 0) or 0)
    return {
        "observed": True,
        "completion_rate": round(int(bucket.get("completed_workflows", 0) or 0) / observed, 4),
        "deterministic_pass_rate": round(int(bucket.get("deterministic_passes", 0) or 0) / observed, 4),
        "semantic_pass_rate": round(int(bucket.get("semantic_passes", 0) or 0) / observed, 4),
        "avg_downstream_repairs": round(int(bucket.get("downstream_repairs", 0) or 0) / observed, 4),
        "avg_workflow_tokens": (
            round(int(bucket.get("workflow_total_tokens", 0) or 0) / token_known, 2)
            if token_known else UNKNOWN
        ),
        "avg_workflow_wall_time_ms": (
            round(int(bucket.get("workflow_wall_time_ms", 0) or 0) / wall_known, 2)
            if wall_known else UNKNOWN
        ),
    }


def _number(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _strict_outcome(value: dict[str, object]) -> str:
    privacy_value = value.get("privacy", {})
    strict = privacy_value.get("strict-confidential", {}) if isinstance(privacy_value, dict) else {}
    return str(strict.get("outcome", "UNKNOWN")) if isinstance(strict, dict) else "UNKNOWN"


def recommend_role(role: str, candidates: dict[str, dict[str, object]]) -> dict[str, object]:
    observed: list[dict[str, object]] = []
    for value in candidates.values():
        rates = candidate_rates(value)
        value["rates"] = rates
        if rates["observed"]:
            observed.append(value)
    if not observed:
        return {
            "role": role,
            "status": "insufficient-evidence",
            "recommended_candidate": "",
            "reason": "no selected replay/live result observed this role",
        }

    strict_eligible = [value for value in observed if _strict_outcome(value) == "ALLOW"]
    pool = strict_eligible or observed

    def rank(value: dict[str, object]) -> tuple[object, ...]:
        rates = value.get("rates", {})
        assert isinstance(rates, dict)
        return (
            -_number(rates.get("deterministic_pass_rate"), 0),
            -_number(rates.get("semantic_pass_rate"), 0),
            int(value.get("provider_failures", 0) or 0),
            _number(rates.get("avg_downstream_repairs"), 999),
            CLASS_RANK.get(str(value.get("candidate_class", "unknown")), 99),
            _number(rates.get("avg_workflow_wall_time_ms"), float("inf")),
            str(value.get("candidate_id", "")),
        )

    chosen = sorted(pool, key=rank)[0]
    rates = chosen.get("rates", {})
    privacy_value = chosen.get("privacy", {})
    strict = privacy_value.get("strict-confidential", {}) if isinstance(privacy_value, dict) else {}
    status = (
        "qualified-by-observed-workflow-evidence"
        if strict_eligible
        else "qualified-evidence-but-strict-confidential-needs-exception"
    )
    return {
        "role": role,
        "status": status,
        "recommended_candidate": chosen.get("candidate_id", ""),
        "candidate_class": chosen.get("candidate_class", "unknown"),
        "model": chosen.get("model", ""),
        "profiles": chosen.get("profiles", []),
        "evidence": rates,
        "strict_confidential": strict,
        "note": (
            "Workflow-level correctness is attributed conservatively to every observed role candidate. "
            "Privacy eligibility is applied before quality/cost ranking; more representative live cases "
            "are required before small differences should drive defaults."
        ),
    }


def benchmark_coverage(
    role_candidates: dict[str, dict[str, dict[str, object]]],
) -> dict[str, object]:
    coverage: dict[str, object] = {}
    missing: list[str] = []
    for role in REQUIRED_CANDIDATE_ROLES:
        candidates = role_candidates.get(role, {})
        local = [
            value for value in candidates.values()
            if value.get("candidate_class") == "local" and int(value.get("observed_runs", 0) or 0) > 0
        ]
        free = [
            value for value in candidates.values()
            if value.get("candidate_class") == "free-cloud" and int(value.get("observed_runs", 0) or 0) > 0
        ]
        coverage[role] = {
            "local_observed": bool(local),
            "free_cloud_observed": bool(free),
            "complete": bool(local and free),
        }
        if not local:
            missing.append(f"{role}:local")
        if not free:
            missing.append(f"{role}:free-cloud")
    return {
        "roles": coverage,
        "complete": not missing,
        "missing": missing,
    }


def extend_aggregate(
    aggregate_value: dict[str, object],
    results: list[dict[str, object]],
) -> dict[str, object]:
    role_candidates: dict[str, dict[str, dict[str, object]]] = {}
    for result in results:
        add_result(role_candidates, result)

    recommendations = {
        role: recommend_role(role, role_candidates.get(role, {}))
        for role in ROUTING_ROLES
    }
    evidence_gaps = [
        role
        for role, recommendation in recommendations.items()
        if recommendation.get("status") == "insufficient-evidence"
    ]
    aggregate_value["role_candidates"] = role_candidates
    aggregate_value["benchmark_coverage"] = benchmark_coverage(role_candidates)
    aggregate_value["routing_recommendation"] = {
        "roles": recommendations,
        "evidence_gaps": evidence_gaps,
        "policy": {
            "selection_order": "privacy admissibility -> correctness -> semantic quality -> repair burden -> cost class -> efficiency",
            "fallback": "keep OpenAI/frontier available as an explicit fallback; never silently substitute it for a failed free route",
            "verifier_independence": "prefer a verifier candidate/model different from the implementer when qualified evidence exists",
            "privacy": "the repository privacy gate is authoritative over benchmark preference",
        },
    }
    return aggregate_value


def render_role_candidates(groups: object) -> list[str]:
    lines = ["## Role-routing evidence", ""]
    if not isinstance(groups, dict):
        return [*lines, "- (none)", ""]
    for role in ROUTING_ROLES:
        lines.extend([f"### {role}", ""])
        candidates = groups.get(role, {})
        if not isinstance(candidates, dict) or not candidates:
            lines.extend(["- no candidate configured in selected results", ""])
            continue
        for candidate_id, raw in sorted(candidates.items()):
            value = raw if isinstance(raw, dict) else {}
            rates = value.get("rates", {}) if isinstance(value.get("rates"), dict) else candidate_rates(value)
            lines.append(
                f"- `{candidate_id}` ({value.get('candidate_class', 'unknown')}): "
                f"observed={value.get('observed_runs', 0)}, completion={rates.get('completion_rate', UNKNOWN)}, "
                f"deterministic={rates.get('deterministic_pass_rate', UNKNOWN)}, "
                f"semantic={rates.get('semantic_pass_rate', UNKNOWN)}, "
                f"repairs={rates.get('avg_downstream_repairs', UNKNOWN)}, "
                f"provider_failures={value.get('provider_failures', 0)}"
            )
            privacy_value = value.get("privacy", {})
            strict = privacy_value.get("strict-confidential", {}) if isinstance(privacy_value, dict) else {}
            if isinstance(strict, dict):
                lines.append(
                    f"  - strict-confidential: {strict.get('outcome', UNKNOWN)}; "
                    f"{strict.get('reason', '') or strict.get('enforcement_state', '')}"
                )
            lines.append(
                f"  - request limits: `{json.dumps(value.get('request_limits', {}), sort_keys=True)}`"
            )
        lines.append("")
    return lines


def render_recommendation(value: object, coverage: object) -> list[str]:
    lines = ["## Routing recommendation", ""]
    if not isinstance(value, dict):
        return [*lines, "- unavailable", ""]
    roles = value.get("roles", {})
    if isinstance(roles, dict):
        for role in ROUTING_ROLES:
            item = roles.get(role, {})
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("recommended_candidate", "")) or "(needs benchmark evidence)"
            lines.append(f"- **{role}**: {candidate} — {item.get('status', UNKNOWN)}")
    if isinstance(coverage, dict) and not coverage.get("complete", False):
        missing = coverage.get("missing", [])
        if isinstance(missing, list) and missing:
            lines.extend(
                [
                    "",
                    "Acceptance-evidence gap: " + ", ".join(str(item) for item in missing) + ".",
                ]
            )
    lines.extend(
        [
            "",
            "The privacy gate is authoritative. A benchmark winner that is not admissible for the repository's active privacy profile is not an automatic route.",
            "",
        ]
    )
    return lines

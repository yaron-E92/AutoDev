from __future__ import annotations

import json
from pathlib import Path

from automation import eval_harness_core as _core
from automation import privacy
from automation.eval_harness_core import *  # noqa: F401,F403
from automation.model_providers import model_config_from_values


_BASE_LOAD_PROFILES = _core.load_profiles
_BASE_AGGREGATE = _core.aggregate
_BASE_RENDER_MARKDOWN = _core.render_markdown
_BASE_WRITE_RESULTS = _core.write_results
DEFAULT_PROVIDER_FACTS = _core.REPO_ROOT / "benchmarks" / "eval" / "provider-facts.json"
ROUTING_ROLES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
CLASS_RANK = {
    "local": 0,
    "free-cloud": 1,
    "cloud-plan-dependent": 2,
    "frontier-baseline": 3,
    "cloud": 4,
    "unknown": 5,
}


def _add_group(
    groups: dict[str, dict[str, object]],
    key: str,
    result: dict[str, object],
) -> None:
    name = key or UNKNOWN
    bucket = groups.setdefault(
        name,
        {"runs": 0, "completed": 0, "profiles": []},
    )
    bucket["runs"] = int(bucket["runs"]) + 1
    bucket["completed"] = int(bucket["completed"]) + int(result.get("status") == "completed")
    profiles = bucket["profiles"]
    if isinstance(profiles, list):
        profile = str(result.get("profile", ""))
        if profile and profile not in profiles:
            profiles.append(profile)


def load_provider_facts(path: Path = DEFAULT_PROVIDER_FACTS) -> dict[str, object]:
    value = _core.read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise EvalError(f"unsupported provider-facts schema in {path}")
    facts = value.get("facts")
    if not isinstance(facts, dict):
        raise EvalError(f"provider facts must contain an object: {path}")
    return value


def _fact_for_role(
    raw: dict[str, object],
    safe: dict[str, object],
    facts: dict[str, object],
) -> tuple[str, dict[str, object]]:
    endpoint = str(safe.get("endpoint", "")).casefold()
    model = str(safe.get("model", "")).casefold()
    command = str(raw.get("command", "")).casefold()
    items = facts.get("facts", {})
    assert isinstance(items, dict)

    if "api.groq.com" in endpoint:
        key = "groq/openai-gpt-oss-20b" if model == "openai/gpt-oss-20b" else "groq/generic"
    elif "openrouter.ai" in endpoint:
        key = "openrouter/free" if bool(safe.get("free_only")) or model.endswith(":free") else "openrouter/generic"
    elif "ollama " in f" {command} " or command.startswith("ollama "):
        key = "ollama/cloud" if ":cloud" in model or ":cloud" in command else "ollama/local"
    elif command.startswith("codex ") or " codex " in f" {command} ":
        key = "openai/codex-baseline"
    else:
        key = "unknown"
    raw_fact = items.get(key, {})
    return key, dict(raw_fact) if isinstance(raw_fact, dict) else {}


def _candidate_class(key: str, fact: dict[str, object]) -> str:
    explicit = str(fact.get("candidate_class", "")).strip()
    if explicit:
        return explicit
    if key == "ollama/local":
        return "local"
    if key in {"groq/openai-gpt-oss-20b", "openrouter/free"}:
        return "free-cloud"
    if key == "ollama/cloud":
        return "cloud-plan-dependent"
    if key == "openai/codex-baseline":
        return "frontier-baseline"
    return "cloud" if key != "unknown" else "unknown"


def _privacy_preview(config, *, role: str, profile_name: str) -> dict[str, object]:
    policy = privacy.PrivacyPolicy(
        profile=profile_name,
        consent_mode="deny",
        source="evaluation-preview",
    )
    provider_id = privacy._provider_id(config)
    model = str(config.model or "")
    route = f"{provider_id}/{model}" if model else provider_id
    if profile_name == "off":
        decision = privacy.PrivacyDecision(
            "ALLOW",
            role,
            route,
            provider_id,
            model,
            privacy._scope(provider_id),
            enforcement_state="not-required",
            reason="privacy policy disabled",
        )
        return decision.safe_metadata()

    if policy.local_only and provider_id != "local":
        decision = privacy.PrivacyDecision(
            "BLOCK",
            role,
            route,
            provider_id,
            model,
            privacy._scope(provider_id),
            reason="local-only forbids cloud processing",
        )
        return decision.safe_metadata()

    if provider_id == "openrouter":
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
            controls=[
                f"provider.{key}={json.dumps(value)}"
                for key, value in privacy._openrouter_controls(policy).items()
            ],
            reason=(
                "runtime request controls are available, but benchmark preview has no repository/account "
                "attestation for OpenRouter-owned logging/data-sharing settings"
            ),
        )
        if not policy.no_training and not policy.zero_retention:
            decision.outcome = "ALLOW"
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
        enforcement_state="verified-effective" if provider_id == "local" else "enforced-by-provider-contract",
    )
    if not privacy._satisfies(policy, decision):
        decision.outcome = "CONSENT_REQUIRED"
        decision.reason = privacy._gap(policy, decision)
    return decision.safe_metadata()


def _role_benchmark_metadata(
    role: str,
    raw: dict[str, object],
    safe: dict[str, object],
    facts: dict[str, object],
) -> dict[str, object]:
    key, fact = _fact_for_role(raw, safe, facts)
    try:
        config = model_config_from_values(role, raw)
        privacy_matrix = {
            name: _privacy_preview(config, role=role, profile_name=name)
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
        "candidate_class": _candidate_class(key, fact),
        "fact_key": key,
        "request_limits": fact.get("request_limits", {"status": UNKNOWN}),
        "tool_contract": fact.get("tool_contract", UNKNOWN),
        "availability": fact.get("availability", UNKNOWN),
        "source": fact.get("source", []),
        "checked_at": fact.get("checked_at", facts.get("checked_at", UNKNOWN)),
        "privacy": privacy_matrix,
    }


def load_profiles(path: Path, *, repo_root: Path = _core.REPO_ROOT) -> dict[str, dict[str, object]]:
    profiles = _BASE_LOAD_PROFILES(path, repo_root=repo_root)
    facts = load_provider_facts(repo_root / "benchmarks" / "eval" / "provider-facts.json")
    for profile in profiles.values():
        raw = _core.read_json(Path(str(profile["provider_path"])))
        raw_roles = raw.get("roles", {}) if isinstance(raw, dict) else {}
        safe_summary = profile.get("provider_summary", {})
        safe_roles = safe_summary.get("roles", {}) if isinstance(safe_summary, dict) else {}
        benchmark_roles: dict[str, object] = {}
        if isinstance(raw_roles, dict) and isinstance(safe_roles, dict):
            for role, raw_role in raw_roles.items():
                safe_role = safe_roles.get(role, {})
                if isinstance(raw_role, dict) and isinstance(safe_role, dict):
                    benchmark_roles[str(role)] = _role_benchmark_metadata(
                        str(role), raw_role, safe_role, facts
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
    role_groups = groups.setdefault(role, {})
    return role_groups.setdefault(
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


def _add_candidate_observation(
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
            bucket["semantic_passes"] = int(bucket["semantic_passes"]) + int(semantic.get("verdict") == "pass")
        if isinstance(failures, list):
            bucket["provider_failures"] = int(bucket["provider_failures"]) + sum(
                1 for failure in failures if isinstance(failure, dict) and failure.get("role") == role
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


def _candidate_rates(bucket: dict[str, object]) -> dict[str, object]:
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


def _recommend_for_role(role: str, candidates: dict[str, dict[str, object]]) -> dict[str, object]:
    observed: list[dict[str, object]] = []
    for value in candidates.values():
        rates = _candidate_rates(value)
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

    def rank(value: dict[str, object]) -> tuple[object, ...]:
        rates = value.get("rates", {})
        assert isinstance(rates, dict)
        strict = value.get("privacy", {}).get("strict-confidential", {}) if isinstance(value.get("privacy"), dict) else {}
        strict_outcome = str(strict.get("outcome", "UNKNOWN")) if isinstance(strict, dict) else "UNKNOWN"
        strict_penalty = 0 if strict_outcome == "ALLOW" else 1
        return (
            -float(rates.get("deterministic_pass_rate", 0) or 0),
            -float(rates.get("semantic_pass_rate", 0) or 0),
            int(value.get("provider_failures", 0) or 0),
            float(rates.get("avg_downstream_repairs", 999) or 999),
            strict_penalty,
            CLASS_RANK.get(str(value.get("candidate_class", "unknown")), 99),
            str(value.get("candidate_id", "")),
        )

    chosen = sorted(observed, key=rank)[0]
    rates = chosen.get("rates", {})
    strict = chosen.get("privacy", {}).get("strict-confidential", {}) if isinstance(chosen.get("privacy"), dict) else {}
    return {
        "role": role,
        "status": "qualified-by-observed-workflow-evidence",
        "recommended_candidate": chosen.get("candidate_id", ""),
        "candidate_class": chosen.get("candidate_class", "unknown"),
        "model": chosen.get("model", ""),
        "profiles": chosen.get("profiles", []),
        "evidence": rates,
        "strict_confidential": strict,
        "note": (
            "Workflow-level correctness is attributed conservatively to every observed role candidate; "
            "use more live cases before treating small differences as significant."
        ),
    }


def aggregate(
    results: list[dict[str, object]],
    cases: dict[str, dict[str, object]],
) -> dict[str, object]:
    value = _BASE_AGGREGATE(results, cases)
    transports: dict[str, dict[str, object]] = {}
    models: dict[str, dict[str, object]] = {}
    role_candidates: dict[str, dict[str, dict[str, object]]] = {}
    for result in results:
        reproducibility = result.get("reproducibility", {})
        provider = reproducibility.get("provider_summary", {}) if isinstance(reproducibility, dict) else {}
        roles = provider.get("roles", {}) if isinstance(provider, dict) else {}
        run_transports: set[str] = set()
        run_models: set[str] = set()
        if isinstance(roles, dict):
            for role in roles.values():
                if not isinstance(role, dict):
                    continue
                transport = str(role.get("transport", "")).strip()
                model = str(role.get("model", "")).strip()
                if transport:
                    run_transports.add(transport)
                if model:
                    run_models.add(model)
        for transport in sorted(run_transports):
            _add_group(transports, transport, result)
        for model in sorted(run_models):
            _add_group(models, model, result)
        _add_candidate_observation(role_candidates, result)

    recommendations = {
        role: _recommend_for_role(role, role_candidates.get(role, {}))
        for role in ROUTING_ROLES
    }
    gaps = [
        role for role, recommendation in recommendations.items()
        if recommendation.get("status") == "insufficient-evidence"
    ]
    value["provider_transports"] = transports
    value["models"] = models
    value["role_candidates"] = role_candidates
    value["routing_recommendation"] = {
        "roles": recommendations,
        "evidence_gaps": gaps,
        "policy": {
            "quality_order": "correctness -> semantic quality -> repair burden -> privacy admissibility -> cost class -> efficiency",
            "fallback": "keep OpenAI/frontier available as an explicit fallback; never substitute it silently for a failed free route",
            "verifier_independence": "prefer a verifier candidate/model different from the implementer when qualified evidence exists",
            "privacy": "repository privacy gate remains authoritative over benchmark preference",
        },
    }
    return value


def _render_groups(title: str, groups: object) -> list[str]:
    lines = [f"## {title}", ""]
    if not isinstance(groups, dict) or not groups:
        return [*lines, "- (none)", ""]
    for name, raw in sorted(groups.items()):
        value = raw if isinstance(raw, dict) else {}
        profiles = value.get("profiles", [])
        profiles_text = ", ".join(str(item) for item in profiles) if isinstance(profiles, list) else ""
        lines.append(
            f"- `{name}`: runs={value.get('runs', 0)}, completed={value.get('completed', 0)}"
            + (f", profiles={profiles_text}" if profiles_text else "")
        )
    lines.append("")
    return lines


def _render_role_candidates(groups: object) -> list[str]:
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
            rates = value.get("rates", {}) if isinstance(value.get("rates"), dict) else _candidate_rates(value)
            lines.append(
                f"- `{candidate_id}` ({value.get('candidate_class', 'unknown')}): "
                f"observed={value.get('observed_runs', 0)}, completion={rates.get('completion_rate', UNKNOWN)}, "
                f"deterministic={rates.get('deterministic_pass_rate', UNKNOWN)}, semantic={rates.get('semantic_pass_rate', UNKNOWN)}, "
                f"repairs={rates.get('avg_downstream_repairs', UNKNOWN)}, provider_failures={value.get('provider_failures', 0)}"
            )
            privacy_value = value.get("privacy", {})
            strict = privacy_value.get("strict-confidential", {}) if isinstance(privacy_value, dict) else {}
            if isinstance(strict, dict):
                lines.append(
                    f"  - strict-confidential: {strict.get('outcome', UNKNOWN)}; "
                    f"{strict.get('reason', '') or strict.get('enforcement_state', '')}"
                )
            lines.append(f"  - request limits: `{json.dumps(value.get('request_limits', {}), sort_keys=True)}`")
        lines.append("")
    return lines


def _render_recommendation(value: object) -> list[str]:
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
    gaps = value.get("evidence_gaps", [])
    if isinstance(gaps, list) and gaps:
        lines.append("")
        lines.append("Evidence still required for: " + ", ".join(str(item) for item in gaps) + ".")
    lines.extend(
        [
            "",
            "The privacy gate is authoritative: a benchmark winner that is not admissible for the repository's active privacy profile is not an automatic route.",
            "",
        ]
    )
    return lines


def render_markdown(
    results: list[dict[str, object]],
    aggregate_value: dict[str, object],
) -> str:
    base = _BASE_RENDER_MARKDOWN(results, aggregate_value).rstrip()
    extra = [
        "",
        *_render_groups(
            "Aggregate by provider transport",
            aggregate_value.get("provider_transports", {}),
        ),
        *_render_groups(
            "Aggregate by model",
            aggregate_value.get("models", {}),
        ),
        *_render_role_candidates(aggregate_value.get("role_candidates", {})),
        *_render_recommendation(aggregate_value.get("routing_recommendation", {})),
    ]
    return base + "\n" + "\n".join(extra).rstrip() + "\n"


def write_results(
    output_root: Path,
    results: list[dict[str, object]],
    aggregate_value: dict[str, object],
) -> None:
    _BASE_WRITE_RESULTS(output_root, results, aggregate_value)
    recommendation = aggregate_value.get("routing_recommendation", {})
    (output_root / "routing-recommendation.json").write_text(
        json.dumps(_core.redact(recommendation), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "routing-recommendation.md").write_text(
        "# AutoDev role-routing recommendation\n\n"
        + "\n".join(_render_recommendation(recommendation)),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    _core.load_profiles = load_profiles
    _core.aggregate = aggregate
    _core.render_markdown = render_markdown
    _core.write_results = write_results
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

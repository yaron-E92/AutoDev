from __future__ import annotations

import json
from pathlib import Path

from automation import eval_harness_core as _core
from automation import role_routing_benchmark as routing
from automation.eval_harness_core import *  # noqa: F401,F403


_BASE_LOAD_PROFILES = _core.load_profiles
_BASE_LOAD_REPLAY = _core.load_replay
_BASE_AGGREGATE = _core.aggregate
_BASE_RENDER_MARKDOWN = _core.render_markdown
_BASE_WRITE_RESULTS = _core.write_results


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


def load_profiles(path: Path, *, repo_root: Path = _core.REPO_ROOT) -> dict[str, dict[str, object]]:
    profiles = _BASE_LOAD_PROFILES(path, repo_root=repo_root)
    return routing.enrich_profiles(profiles, repo_root=repo_root)


def load_replay(
    case: dict[str, object],
    profile: dict[str, object],
    *,
    cases_path: Path,
) -> dict[str, object]:
    result = _BASE_LOAD_REPLAY(case, profile, cases_path=cases_path)
    reproducibility = result.setdefault("reproducibility", {})
    if isinstance(reproducibility, dict):
        reproducibility.setdefault("provider_summary", profile.get("provider_summary", {}))
    return result


def _routing_safe_result(result: dict[str, object]) -> dict[str, object]:
    """Do not count a replay as evidence for an unresolved configured model placeholder."""
    reproducibility = result.get("reproducibility", {})
    provider = reproducibility.get("provider_summary", {}) if isinstance(reproducibility, dict) else {}
    benchmark = provider.get("benchmark", {}) if isinstance(provider, dict) else {}
    roles = benchmark.get("roles", {}) if isinstance(benchmark, dict) else {}
    unresolved = {
        str(role)
        for role, metadata in roles.items()
        if isinstance(metadata, dict) and "REPLACE_WITH" in str(metadata.get("candidate_id", ""))
    } if isinstance(roles, dict) else set()
    if not unresolved:
        return result

    clone = dict(result)
    efficiency = dict(result.get("efficiency", {})) if isinstance(result.get("efficiency"), dict) else {}
    calls = dict(efficiency.get("model_calls_by_role", {})) if isinstance(efficiency.get("model_calls_by_role"), dict) else {}
    for role in unresolved:
        calls[role] = 0
    efficiency["model_calls_by_role"] = calls
    clone["efficiency"] = efficiency
    return clone


def aggregate(
    results: list[dict[str, object]],
    cases: dict[str, dict[str, object]],
) -> dict[str, object]:
    value = _BASE_AGGREGATE(results, cases)
    transports: dict[str, dict[str, object]] = {}
    models: dict[str, dict[str, object]] = {}
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
    value["provider_transports"] = transports
    value["models"] = models
    return routing.extend_aggregate(value, [_routing_safe_result(result) for result in results])


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
        *routing.render_role_candidates(aggregate_value.get("role_candidates", {})),
        *routing.render_recommendation(
            aggregate_value.get("routing_recommendation", {}),
            aggregate_value.get("benchmark_coverage", {}),
        ),
    ]
    return base + "\n" + "\n".join(extra).rstrip() + "\n"


def write_results(
    output_root: Path,
    results: list[dict[str, object]],
    aggregate_value: dict[str, object],
) -> None:
    _BASE_WRITE_RESULTS(output_root, results, aggregate_value)
    recommendation = aggregate_value.get("routing_recommendation", {})
    coverage = aggregate_value.get("benchmark_coverage", {})
    payload = {
        "routing_recommendation": recommendation,
        "benchmark_coverage": coverage,
    }
    (output_root / "routing-recommendation.json").write_text(
        json.dumps(_core.redact(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "routing-recommendation.md").write_text(
        "# AutoDev role-routing recommendation\n\n"
        + "\n".join(routing.render_recommendation(recommendation, coverage)),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    _core.load_profiles = load_profiles
    _core.load_replay = load_replay
    _core.aggregate = aggregate
    _core.render_markdown = render_markdown
    _core.write_results = write_results
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

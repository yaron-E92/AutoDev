from __future__ import annotations

from automation import eval_harness_core as _core
from automation.eval_harness_core import *  # noqa: F401,F403


_BASE_AGGREGATE = _core.aggregate
_BASE_RENDER_MARKDOWN = _core.render_markdown


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
    ]
    return base + "\n" + "\n".join(extra).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    _core.aggregate = aggregate
    _core.render_markdown = render_markdown
    return _core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

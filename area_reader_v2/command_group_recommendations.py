from __future__ import annotations

from collections.abc import Iterable

DEFAULT_RECOMMENDED_COMMAND_GROUPS = [
    "env",
    "dotnet-solution",
    "node-root",
    "markdown-smoke",
]

CONDITIONAL_COMMAND_GROUPS = {
    "api-client-generate": "Run if API/OpenAPI/client generation is touched.",
    "web-app": "Run for web-specific issues or if root scripts are insufficient.",
    "maui-android-doctor": "Run for MAUI/mobile issues or explicit mobile verification.",
    "maui-android-build": "Run for MAUI/mobile issues when Android SDK is available.",
    "ci-manual-reference": "Reference only; do not run by default.",
}

ALL_COMMAND_GROUPS = [
    *DEFAULT_RECOMMENDED_COMMAND_GROUPS,
    *CONDITIONAL_COMMAND_GROUPS.keys(),
]

API_CLIENT_TERMS = (
    "api client generation",
    "openapi",
    "swagger",
    "generated client",
    "api contract",
    "api contracts",
    "typescript client",
)

WEB_TERMS = (
    "react",
    "vite",
    "frontend",
    "browser",
    "component",
    "web shell",
)

MAUI_CONTEXT_TERMS = (
    "maui",
    "mobile",
    "android",
)

MAUI_WORK_INTENT_TERMS = (
    "build",
    "run",
    "debug",
    "fix",
    "modify",
    "update",
    "verify",
    "validate",
    "test",
    "tooling",
    "device",
    "emulator",
    "xaml",
)

MAUI_EXPLICIT_WORK_TERMS = (
    "android device",
    "android emulator",
    "emulator",
    "mobile build",
    "mobile run",
    "mobile tooling",
    "mobile ui",
    "mobile verification",
    "mobile-specific behavior",
    "xaml",
)

MAUI_INVENTORY_PHRASES = (
    "including backend, web, maui/mobile/desktop if present",
    "backend, web, maui/mobile/desktop if present",
    "maui/mobile/desktop if present",
)

API_CONTRACT_PATH_MARKERS = (
    "api-client",
    "apiclient",
    "openapi",
    "swagger",
    "generated",
)


def recommend_command_groups(
    *,
    issue_text: str,
    changed_paths: Iterable[str],
    web_build_errors: str = "",
    root_node_scripts_sufficient: bool = True,
    android_sdk_available: bool = False,
    user_requested_mobile_verification: bool = False,
    available_command_groups: Iterable[str] | None = None,
) -> dict[str, object]:
    """Return JSON-ready command-group metadata for area-reader v2 output."""

    normalized_issue = issue_text.casefold()
    normalized_web_errors = web_build_errors.casefold()
    normalized_paths = tuple(path.replace("\\", "/").casefold() for path in changed_paths)
    available = list(available_command_groups) if available_command_groups is not None else list(ALL_COMMAND_GROUPS)
    available_set = set(available)

    recommended = [
        group
        for group in DEFAULT_RECOMMENDED_COMMAND_GROUPS
        if group in available_set
    ]

    if _api_client_relevant(normalized_issue, normalized_web_errors, normalized_paths):
        recommended.append("api-client-generate")

    if _web_relevant(normalized_issue, normalized_paths, root_node_scripts_sufficient):
        recommended.append("web-app")

    maui_relevant = _maui_relevant(
        normalized_issue,
        normalized_paths,
        user_requested_mobile_verification,
    )
    if maui_relevant:
        recommended.append("maui-android-doctor")

    if maui_relevant and android_sdk_available:
        recommended.append("maui-android-build")

    return {
        "available_command_groups": available,
        "recommended_command_groups": _filter_unique(recommended, available_set),
        "conditional_command_groups": {
            name: reason
            for name, reason in CONDITIONAL_COMMAND_GROUPS.items()
            if name in available_set
        },
    }


def _api_client_relevant(
    issue_text: str,
    web_build_errors: str,
    changed_paths: Iterable[str],
) -> bool:
    if _contains_any(issue_text, API_CLIENT_TERMS):
        return True

    if _contains_any(web_build_errors, ("generated api client", "stale generated", "missing generated")):
        return True

    return any(_is_api_contract_path(path) for path in changed_paths)


def _web_relevant(
    issue_text: str,
    changed_paths: Iterable[str],
    root_node_scripts_sufficient: bool,
) -> bool:
    if any(path.startswith("phoodab/apps/web/") for path in changed_paths):
        return True

    if _contains_any(issue_text, WEB_TERMS):
        return True

    return not root_node_scripts_sufficient


def _maui_relevant(
    issue_text: str,
    changed_paths: Iterable[str],
    user_requested_mobile_verification: bool,
) -> bool:
    if user_requested_mobile_verification:
        return True

    if any(
        path.startswith("phoodab/apps/mobile/")
        or path.startswith("phoodab/apps/mobile-shared/")
        for path in changed_paths
    ):
        return True

    scoped_issue_text = _remove_maui_inventory_mentions(issue_text)
    if "phoodab/apps/mobile" in scoped_issue_text or "phoodab/apps/mobile-shared" in scoped_issue_text:
        return True

    return _maui_text_relevant(scoped_issue_text)


def _is_api_contract_path(path: str) -> bool:
    if path.startswith("phoodab/packages/api-client/"):
        return True

    return _contains_any(path, API_CONTRACT_PATH_MARKERS)


def _remove_maui_inventory_mentions(value: str) -> str:
    result = value
    for phrase in MAUI_INVENTORY_PHRASES:
        result = result.replace(phrase, "")
    return result


def _maui_text_relevant(issue_text: str) -> bool:
    if _contains_any(issue_text, MAUI_EXPLICIT_WORK_TERMS):
        return True

    return _contains_any(issue_text, MAUI_CONTEXT_TERMS) and _contains_any(issue_text, MAUI_WORK_INTENT_TERMS)


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    return any(term in value for term in terms)


def _filter_unique(values: Iterable[str], allowed: set[str]) -> list[str]:
    filtered = []
    seen = set()
    for value in values:
        if value in allowed and value not in seen:
            filtered.append(value)
            seen.add(value)
    return filtered

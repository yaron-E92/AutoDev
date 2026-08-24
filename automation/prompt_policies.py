from __future__ import annotations

from automation.provider_contract import ProviderError
from automation.model_roles import MODEL_ROLES


PONYTAIL_SOURCE = "DietrichGebert/ponytail"
PONYTAIL_SOURCE_VERSION = "v4.8.4"
PONYTAIL_SOURCE_COMMIT = "bc9ee949d5f439e8b9f3bb92c6d6d3d1e6ebd324"
PROMPT_POLICY_VERSION = "autodev-ponytail-v1"
SUPPORTED_POLICY_MODES = {"off", "lite", "full", "review"}
DEFAULT_POLICY_MODES = {
    "reader": "off",
    "synthesizer": "lite",
    "planner": "lite",
    "implementer": "full",
    "fixer": "full",
    "verifier": "review",
}

_POLICY_TEXT = {
    "off": "",
    "lite": (
        "Understand the task and preserve every explicit requirement and uncertainty. "
        "Prefer existing behavior, helpers, standard-library features, and repository patterns. "
        "Choose the smallest complete approach, but do not simplify away validation, safety, "
        "or user-visible behavior."
    ),
    "full": (
        "Understand the real flow before changing it. Reuse existing code, standard-library "
        "features, native platform behavior, and installed dependencies before writing new code. "
        "Fix the shared root cause, avoid unrequested abstractions, dependencies, boilerplate, and "
        "unrelated cleanup, and use the fewest files needed for a complete solution. Never trade "
        "away explicit requirements, security, data integrity, accessibility, trust-boundary "
        "validation, or error handling that prevents data loss."
    ),
    "review": (
        "Review only; do not implement or rewrite the solution. Check whether the patch recreated "
        "existing functionality, added unnecessary abstractions, broadened scope, omitted required "
        "security, data-integrity, accessibility, validation, or error handling, or simplified away "
        "an explicit or user-visible requirement."
    ),
}

_INSERT_BEFORE = (
    "\nOriginal issue:\n",
    "\nIssue:\n",
    "\nSynthesized handoff:\n",
    "\nRepository evidence:\n",
    "\nVerifier input:\n",
    "\nPatch response contract:\n",
    "\nOutput contract:\n",
    "\nOutput only:\n",
)
_POLICY_HEADER = "Role-specific prompt policy ("


def resolve_prompt_policies(file_config: dict[str, object]) -> dict[str, str]:
    section = file_config.get("prompt_policy", {})
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ProviderError("prompt_policy must be an object")

    enabled = section.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ProviderError("prompt_policy.enabled must be true or false")

    role_overrides = section.get("roles", {})
    if role_overrides is None:
        role_overrides = {}
    if not isinstance(role_overrides, dict):
        raise ProviderError("prompt_policy.roles must be an object")

    unknown = sorted(set(role_overrides) - set(MODEL_ROLES))
    if unknown:
        raise ProviderError("unknown prompt policy role(s): " + ", ".join(unknown))

    if not enabled:
        return {role: "off" for role in MODEL_ROLES}

    resolved = dict(DEFAULT_POLICY_MODES)
    for role, value in role_overrides.items():
        mode = str(value).strip().casefold()
        if mode not in SUPPORTED_POLICY_MODES:
            raise ProviderError(f"unsupported prompt policy mode for {role}: {value}")
        resolved[role] = mode
    return resolved


def compose_prompt(role: str, prompt: str, mode: str) -> str:
    if role not in MODEL_ROLES:
        raise ProviderError(f"unknown model role: {role}")
    if mode not in SUPPORTED_POLICY_MODES:
        raise ProviderError(f"unsupported prompt policy mode: {mode}")

    policy = _POLICY_TEXT[mode]
    if not policy or _POLICY_HEADER in prompt:
        return prompt

    indexes = [prompt.find(marker) for marker in _INSERT_BEFORE]
    indexes = [index for index in indexes if index >= 0]
    insertion = min(indexes) if indexes else len(prompt)
    block = (
        f"\n\nRole-specific prompt policy ({mode}; {PROMPT_POLICY_VERSION}):\n"
        f"{policy}\n"
    )
    return prompt[:insertion].rstrip() + block + "\n" + prompt[insertion:].lstrip("\n")


def role_policy_metadata(role: str, policies: dict[str, str]) -> dict[str, object]:
    return {
        "prompt_policy_mode": policies.get(role, DEFAULT_POLICY_MODES[role]),
        "prompt_policy_version": PROMPT_POLICY_VERSION,
        "prompt_policy_source": PONYTAIL_SOURCE,
        "prompt_policy_source_version": PONYTAIL_SOURCE_VERSION,
        "prompt_policy_source_commit": PONYTAIL_SOURCE_COMMIT,
    }


def safe_prompt_policy_metadata(policies: dict[str, str]) -> dict[str, object]:
    return {
        "enabled": any(mode != "off" for mode in policies.values()),
        "policy_version": PROMPT_POLICY_VERSION,
        "source": PONYTAIL_SOURCE,
        "source_version": PONYTAIL_SOURCE_VERSION,
        "source_commit": PONYTAIL_SOURCE_COMMIT,
        "roles": dict(policies),
    }

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from automation import privacy


def evaluate_role(
    repo: Path,
    *,
    role: str,
    model: str,
    opencode_cli: str,
    runner=subprocess.run,
    base_env: dict[str, str] | None = None,
) -> tuple[privacy.PrivacyDecision, dict[str, str]]:
    """Return OpenCode-specific route evidence without authorizing consent/grants."""
    repo = repo.expanduser().resolve()
    policy = privacy.load_policy(repo)
    provider_id, model_id = privacy._split_model(model)
    if provider_id == "ollama":
        provider_id = "ollama-cloud" if privacy._ollama_cloud(model_id) else "local"
    # OpenCode's openai ID can be API-key or OAuth/product-specific.
    if provider_id == "openai":
        provider_id = "openai-opencode"

    provider = provider_id or "unknown"
    route = model or f"{provider}/{model_id}"
    env = dict(base_env or os.environ)

    if not policy.enabled:
        return privacy.PrivacyDecision(
            "ALLOW", role, route, provider, model_id, privacy._scope(provider),
            enforcement_state="not-required", reason="privacy policy disabled",
        ), env

    if policy.local_only and provider != "local":
        return privacy.PrivacyDecision(
            "BLOCK", role, route, provider, model_id, privacy._scope(provider),
            reason="repository privacy profile is local-only; cloud exceptions are forbidden",
        ), env

    if provider == "openrouter":
        controls = privacy._openrouter_controls(policy)
        initial = privacy._debug_config(repo, opencode_cli, runner, env)
        overlay = privacy._openrouter_overlay(initial, model_id, controls)
        env = privacy._merge_inline_config(env, overlay)
        resolved = privacy._debug_config(repo, opencode_cli, runner, env)
        request_verified = privacy._resolved_openrouter_verified(resolved, model_id, policy)
        decision = privacy.PrivacyDecision(
            "ALLOW", role, route, provider, model_id, "routed-cloud",
            training="unknown", retention="unknown",
            policy_source=(
                "https://openrouter.ai/docs/guides/routing/provider-selection; "
                "https://openrouter.ai/docs/guides/privacy/data-collection"
            ),
            enforcement_state="request-verified" if request_verified else "unverified",
            controls=[f"provider.{key}={json.dumps(value)}" for key, value in controls.items()],
            reason=(
                "OpenCode effective config verifies downstream OpenRouter request controls, but "
                "OpenRouter account-level content logging/data-sharing settings must also be verified or attested"
            ),
        )
        privacy._apply_attestation(policy, decision)
        if request_verified and privacy._satisfies(policy, decision):
            return decision, env
        decision.outcome = "CONSENT_REQUIRED"
        decision.reason = privacy._gap(policy, decision)
        return decision, env

    training, retention, duration, source = privacy._classify(provider)
    decision = privacy.PrivacyDecision(
        "ALLOW", role, route, provider, model_id, privacy._scope(provider),
        training, retention, duration, policy_source=source,
        enforcement_state="verified-effective" if provider == "local" else "enforced-by-provider-contract",
    )
    privacy._apply_attestation(policy, decision)
    if privacy._satisfies(policy, decision):
        return decision, env
    decision.outcome = "CONSENT_REQUIRED"
    decision.reason = privacy._gap(policy, decision)
    return decision, env

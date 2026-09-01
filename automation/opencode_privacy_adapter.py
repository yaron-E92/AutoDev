from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from automation import privacy


def _debug_config(
    repo: Path, executable: str, runner, env: dict[str, str]
) -> dict[str, object]:
    completed = runner(
        [executable, "debug", "config"],
        cwd=repo,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if int(getattr(completed, "returncode", 1)) != 0:
        raise privacy.PrivacyError(
            "opencode debug config failed while verifying privacy controls"
        )
    try:
        value = json.loads(str(getattr(completed, "stdout", "") or "{}"))
    except json.JSONDecodeError as exc:
        raise privacy.PrivacyError(
            "opencode debug config returned invalid JSON while verifying privacy controls"
        ) from exc
    if not isinstance(value, dict):
        raise privacy.PrivacyError(
            "opencode debug config returned an unexpected value while verifying privacy controls"
        )
    return value


def _openrouter_overlay(
    config: dict[str, object], model_id: str, controls: dict[str, object]
) -> dict[str, object]:
    if "providers" in config:
        return {"providers": {"openrouter": {"body": {"provider": controls}}}}
    return {
        "provider": {
            "openrouter": {
                "models": {model_id: {"options": {"provider": controls}}}
            }
        }
    }


def _resolved_openrouter_verified(
    config: dict[str, object], model_id: str, policy: privacy.PrivacyPolicy
) -> bool:
    effective: dict[str, object] = {}
    providers = config.get("providers")
    if isinstance(providers, dict):
        openrouter = providers.get("openrouter", {})
        if isinstance(openrouter, dict):
            body = openrouter.get("body", {})
            if isinstance(body, dict) and isinstance(body.get("provider"), dict):
                effective = dict(body["provider"])
            models = openrouter.get("models", {})
            model = models.get(model_id, {}) if isinstance(models, dict) else {}
            if isinstance(model, dict):
                model_body = model.get("body", {})
                if isinstance(model_body, dict) and isinstance(
                    model_body.get("provider"), dict
                ):
                    effective.update(model_body["provider"])
    else:
        providers_v1 = config.get("provider", {})
        openrouter = (
            providers_v1.get("openrouter", {})
            if isinstance(providers_v1, dict)
            else {}
        )
        models = openrouter.get("models", {}) if isinstance(openrouter, dict) else {}
        model = models.get(model_id, {}) if isinstance(models, dict) else {}
        options = model.get("options", {}) if isinstance(model, dict) else {}
        if isinstance(options, dict) and isinstance(options.get("provider"), dict):
            effective = dict(options["provider"])
    return (
        (not policy.no_training or effective.get("data_collection") == "deny")
        and (not policy.zero_retention or effective.get("zdr") is True)
    )


def _merge_inline_config(
    env: dict[str, str], overlay: dict[str, object]
) -> dict[str, str]:
    existing: dict[str, object] = {}
    raw = env.get("OPENCODE_CONFIG_CONTENT", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise privacy.PrivacyError(
                "existing OPENCODE_CONFIG_CONTENT is invalid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise privacy.PrivacyError(
                "existing OPENCODE_CONFIG_CONTENT must be a JSON object"
            )
        existing = parsed
    result = dict(env)
    result["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        privacy._deep_merge(existing, overlay), separators=(",", ":")
    )
    return result

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
        initial = _debug_config(repo, opencode_cli, runner, env)
        overlay = _openrouter_overlay(initial, model_id, controls)
        env = _merge_inline_config(env, overlay)
        resolved = _debug_config(repo, opencode_cli, runner, env)
        request_verified = _resolved_openrouter_verified(resolved, model_id, policy)
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

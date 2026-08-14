from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


PRIVACY_CONFIG = Path(".autodev") / "privacy.json"
PRIVACY_AUDIT = "privacy-audit.jsonl"
POLICY_REVIEWED_AT = "2026-08-14"
POLICY_TTL_DAYS = 180
PROFILES = {"off", "no-training", "strict-confidential", "local-only"}
CONSENT_MODES = {"explicit", "deny"}


class PrivacyError(RuntimeError):
    def __init__(self, message: str, *, classification: str = "privacy_blocked") -> None:
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class PrivacyPolicy:
    profile: str
    consent_mode: str
    source: str

    @property
    def enabled(self) -> bool:
        return self.profile != "off"

    @property
    def no_training(self) -> bool:
        return self.profile in {"no-training", "strict-confidential", "local-only"}

    @property
    def zero_retention(self) -> bool:
        return self.profile in {"strict-confidential", "local-only"}

    @property
    def local_only(self) -> bool:
        return self.profile == "local-only"


@dataclass
class PrivacyDecision:
    outcome: str
    role: str
    route: str
    provider: str
    model: str
    route_scope: str
    training: str = "unknown"
    retention: str = "unknown"
    retention_duration: str = ""
    policy_source: str = ""
    enforcement_state: str = "unverified"
    controls: list[str] = field(default_factory=list)
    consent_scope: str = ""
    reason: str = ""

    def safe_metadata(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "role": self.role,
            "route": self.route,
            "provider": self.provider,
            "model": self.model,
            "route_scope": self.route_scope,
            "training_on_customer_content": self.training,
            "customer_content_retention": self.retention,
            "retention_duration": self.retention_duration,
            "policy_source": self.policy_source,
            "policy_checked_at": POLICY_REVIEWED_AT,
            "enforcement_state": self.enforcement_state,
            "enforcement_controls": list(self.controls),
            "consent_scope": self.consent_scope,
            "reason": self.reason,
        }


# These are reviewed policy facts, not permanent trust decisions. If stale, the gate fails closed.
_PROVIDER_POLICY = {
    "groq": (
        "denied",
        "bounded",
        "up to 30 days for reliability/abuse monitoring unless account ZDR is enabled",
        "https://console.groq.com/docs/your-data",
    ),
    "openai": (
        "denied",
        "bounded",
        "up to 30 days for API abuse monitoring by default; eligible org/projects may enable ZDR",
        "https://platform.openai.com/docs/models/default-usage-policies-by-endpoint",
    ),
    "ollama-cloud": (
        "denied",
        "zero",
        "transient processing only",
        "https://ollama.com/privacy",
    ),
}


def load_policy(repo: Path | None = None) -> PrivacyPolicy:
    repo = (repo or privacy_repo()).expanduser().resolve()
    # Real repositories default to strict; scratch/test directories remain opt-in unless configured.
    default_profile = "strict-confidential" if (repo / ".git").exists() else "off"
    values: dict[str, object] = {}
    config = repo / PRIVACY_CONFIG
    if config.is_file():
        try:
            raw = json.loads(config.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PrivacyError(f"invalid privacy config JSON: {config}") from exc
        if not isinstance(raw, dict):
            raise PrivacyError(f"privacy config must be a JSON object: {config}")
        values = raw

    repo_profile = str(values.get("profile", default_profile)).strip().casefold() or default_profile
    env_profile = os.environ.get("AUTODEV_PRIVACY_PROFILE", "").strip().casefold()
    profile = _stronger_profile(repo_profile, env_profile) if env_profile else repo_profile
    consent_mode = str(values.get("consent_mode", "explicit")).strip().casefold() or "explicit"
    if profile not in PROFILES:
        raise PrivacyError(f"unsupported privacy profile: {profile}")
    if consent_mode not in CONSENT_MODES:
        raise PrivacyError(f"unsupported privacy consent_mode: {consent_mode}")
    return PrivacyPolicy(profile=profile, consent_mode=consent_mode, source=str(config) if config.is_file() else "default")


def privacy_repo() -> Path:
    explicit = os.environ.get("AUTODEV_TARGET_REPO", "").strip()
    return Path(explicit) if explicit else Path.cwd()


def _stronger_profile(repo_profile: str, env_profile: str) -> str:
    rank = {"off": 0, "no-training": 1, "strict-confidential": 2, "local-only": 3}
    if repo_profile not in rank:
        raise PrivacyError(f"unsupported privacy profile: {repo_profile}")
    if env_profile not in rank:
        raise PrivacyError(f"unsupported AUTODEV_PRIVACY_PROFILE: {env_profile}")
    return env_profile if rank[env_profile] >= rank[repo_profile] else repo_profile


def _fresh() -> bool:
    reviewed = datetime.fromisoformat(POLICY_REVIEWED_AT).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) <= reviewed + timedelta(days=POLICY_TTL_DAYS)


def _split_model(model: str) -> tuple[str, str]:
    provider, sep, model_id = (model or "").partition("/")
    return (provider.casefold(), model_id) if sep else ("", model)


def _ollama_cloud(model: str) -> bool:
    lowered = model.casefold()
    return ":cloud" in lowered or lowered.endswith("-cloud") or "/cloud/" in lowered


def _provider_id(config) -> str:
    model = str(getattr(config, "model", "") or "")
    model_provider, _ = _split_model(model)
    base_url = str(getattr(config, "base_url", "") or "")
    host = (urlparse(base_url).hostname or "").casefold()
    command = str(getattr(config, "command", "") or "").casefold()
    if model_provider == "ollama":
        return "ollama-cloud" if _ollama_cloud(model) else "local"
    if model_provider in {"openrouter", "groq", "openai"}:
        return model_provider
    if "openrouter.ai" in host:
        return "openrouter"
    if host.endswith("groq.com"):
        return "groq"
    if host == "api.openai.com" or host.endswith(".api.openai.com"):
        return "openai"
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "ollama-cloud" if _ollama_cloud(model) else "local"
    if command.startswith("ollama ") or " ollama " in f" {command} ":
        return "ollama-cloud" if _ollama_cloud(model) or ":cloud" in command else "local"
    if str(getattr(config, "provider", "")).casefold() == "mock":
        return "local"
    return model_provider or host or "unknown"


def _scope(provider: str) -> str:
    if provider == "local":
        return "local"
    return "routed-cloud" if provider == "openrouter" else "direct-cloud"


def _classify(provider: str) -> tuple[str, str, str, str]:
    if provider == "local":
        return "denied", "zero", "", "local inference"
    if not _fresh() or provider not in _PROVIDER_POLICY:
        return "unknown", "unknown", "", "unknown or stale policy"
    return _PROVIDER_POLICY[provider]


def _satisfies(policy: PrivacyPolicy, decision: PrivacyDecision) -> bool:
    if policy.local_only and decision.route_scope != "local":
        return False
    if policy.no_training and decision.training != "denied":
        return False
    if policy.zero_retention and decision.retention != "zero":
        return False
    return True


def _gap(policy: PrivacyPolicy, decision: PrivacyDecision) -> str:
    gaps: list[str] = []
    if policy.local_only and decision.route_scope != "local":
        gaps.append("cloud processing is forbidden")
    if policy.no_training and decision.training != "denied":
        gaps.append(f"training policy is {decision.training}")
    if policy.zero_retention and decision.retention != "zero":
        gaps.append(f"customer-content retention is {decision.retention}")
    return "; ".join(gaps) or "privacy requirements could not be verified"


def _openrouter_controls(policy: PrivacyPolicy) -> dict[str, object]:
    controls: dict[str, object] = {}
    if policy.no_training:
        controls["data_collection"] = "deny"
    if policy.zero_retention:
        controls["zdr"] = True
    return controls


def _merge_openrouter(options: dict[str, object], policy: PrivacyPolicy) -> list[str]:
    controls = _openrouter_controls(policy)
    provider = options.get("provider", {})
    if not isinstance(provider, dict):
        raise PrivacyError("request_options.provider must be an object")
    merged = dict(provider)
    merged.update(controls)
    options["provider"] = merged
    return [f"provider.{key}={json.dumps(value)}" for key, value in controls.items()]


def _request_option_sets(provider) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for target in (provider, getattr(provider, "direct_provider", None), getattr(provider, "proxy_provider", None)):
        options = getattr(target, "request_options", None)
        if isinstance(options, dict) and all(options is not existing for existing in found):
            found.append(options)
    return found


def _openrouter_verified(options: dict[str, object], policy: PrivacyPolicy) -> bool:
    provider = options.get("provider", {})
    return isinstance(provider, dict) and (not policy.no_training or provider.get("data_collection") == "deny") and (not policy.zero_retention or provider.get("zdr") is True)


def authorize_direct_call(provider, config, *, role: str, repo: Path | None = None, consent_reader: Callable[[str], str] | None = None) -> PrivacyDecision:
    repo = (repo or privacy_repo()).expanduser().resolve()
    policy = load_policy(repo)
    provider_id = _provider_id(config)
    model = str(getattr(config, "model", "") or "")
    route = f"{provider_id}/{model}" if model else provider_id
    if not policy.enabled:
        decision = PrivacyDecision("ALLOW", role, route, provider_id, model, _scope(provider_id), enforcement_state="not-required", reason="privacy policy disabled")
        _audit(repo, decision)
        return decision

    if provider_id == "openrouter" and not policy.local_only:
        option_sets = _request_option_sets(provider)
        if option_sets:
            controls: list[str] = []
            for options in option_sets:
                controls.extend(_merge_openrouter(options, policy))
            if all(_openrouter_verified(options, policy) for options in option_sets):
                decision = PrivacyDecision(
                    "ALLOW", role, route, provider_id, model, "routed-cloud",
                    "denied" if policy.no_training else "unknown",
                    "zero" if policy.zero_retention else "unknown",
                    policy_source="https://openrouter.ai/docs/guides/routing/provider-selection",
                    enforcement_state="verified-effective",
                    controls=sorted(set(controls)),
                    reason="required OpenRouter data-policy routing controls are present on every request path",
                )
                _audit(repo, decision)
                return decision

    training, retention, duration, source = _classify(provider_id)
    decision = PrivacyDecision(
        "ALLOW", role, route, provider_id, model, _scope(provider_id), training, retention, duration,
        policy_source=source,
        enforcement_state="verified-effective" if provider_id == "local" else "enforced-by-provider-contract",
    )
    if _satisfies(policy, decision):
        _audit(repo, decision)
        return decision
    decision.outcome = "CONSENT_REQUIRED"
    decision.reason = _gap(policy, decision)
    return _consent_or_block(repo, policy, decision, consent_reader)


def authorize_opencode_role(
    repo: Path,
    *,
    role: str,
    model: str,
    opencode_cli: str,
    runner=subprocess.run,
    base_env: dict[str, str] | None = None,
    consent_reader: Callable[[str], str] | None = None,
) -> tuple[PrivacyDecision, dict[str, str]]:
    repo = repo.expanduser().resolve()
    policy = load_policy(repo)
    provider_id, model_id = _split_model(model)
    if provider_id == "ollama":
        provider_id = "ollama-cloud" if _ollama_cloud(model_id) else "local"
    route = model or f"{provider_id}/{model_id}"
    env = dict(base_env or os.environ)
    if not policy.enabled:
        decision = PrivacyDecision("ALLOW", role, route, provider_id or "unknown", model_id, _scope(provider_id or "unknown"), enforcement_state="not-required", reason="privacy policy disabled")
        _audit(repo, decision)
        return decision, env

    if provider_id == "openrouter" and not policy.local_only:
        controls = _openrouter_controls(policy)
        initial = _debug_config(repo, opencode_cli, runner, env)
        overlay = _openrouter_overlay(initial, model_id, controls)
        env = _merge_inline_config(env, overlay)
        resolved = _debug_config(repo, opencode_cli, runner, env)
        if _resolved_openrouter_verified(resolved, model_id, policy):
            decision = PrivacyDecision(
                "ALLOW", role, route, provider_id, model_id, "routed-cloud",
                "denied" if policy.no_training else "unknown",
                "zero" if policy.zero_retention else "unknown",
                policy_source="https://openrouter.ai/docs/guides/routing/provider-selection",
                enforcement_state="verified-effective",
                controls=[f"provider.{key}={json.dumps(value)}" for key, value in controls.items()],
                reason="OpenCode effective config verifies required OpenRouter request controls",
            )
            _audit(repo, decision)
            return decision, env

    training, retention, duration, source = _classify(provider_id or "unknown")
    decision = PrivacyDecision(
        "ALLOW", role, route, provider_id or "unknown", model_id, _scope(provider_id or "unknown"),
        training, retention, duration, policy_source=source,
        enforcement_state="verified-effective" if provider_id == "local" else "enforced-by-provider-contract",
    )
    if _satisfies(policy, decision):
        _audit(repo, decision)
        return decision, env
    decision.outcome = "CONSENT_REQUIRED"
    decision.reason = _gap(policy, decision)
    return _consent_or_block(repo, policy, decision, consent_reader), env


def _debug_config(repo: Path, executable: str, runner, env: dict[str, str]) -> dict[str, object]:
    completed = runner(
        [executable, "debug", "config"], cwd=repo, env=env, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if int(getattr(completed, "returncode", 1)) != 0:
        raise PrivacyError("opencode debug config failed while verifying privacy controls")
    try:
        value = json.loads(str(getattr(completed, "stdout", "") or "{}"))
    except json.JSONDecodeError as exc:
        raise PrivacyError("opencode debug config returned invalid JSON while verifying privacy controls") from exc
    if not isinstance(value, dict):
        raise PrivacyError("opencode debug config returned an unexpected value while verifying privacy controls")
    return value


def _openrouter_overlay(config: dict[str, object], model_id: str, controls: dict[str, object]) -> dict[str, object]:
    # OpenCode v2 uses providers/body. Legacy v1 uses provider/models/options.
    if "providers" in config:
        return {"providers": {"openrouter": {"body": {"provider": controls}}}}
    return {"provider": {"openrouter": {"models": {model_id: {"options": {"provider": controls}}}}}}


def _merge_inline_config(env: dict[str, str], overlay: dict[str, object]) -> dict[str, str]:
    existing: dict[str, object] = {}
    raw = env.get("OPENCODE_CONFIG_CONTENT", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PrivacyError("existing OPENCODE_CONFIG_CONTENT is invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise PrivacyError("existing OPENCODE_CONFIG_CONTENT must be a JSON object")
        existing = parsed
    result = dict(env)
    result["OPENCODE_CONFIG_CONTENT"] = json.dumps(_deep_merge(existing, overlay), separators=(",", ":"))
    return result


def _deep_merge(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)  # type: ignore[arg-type]
        else:
            result[key] = value
    return result


def _resolved_openrouter_verified(config: dict[str, object], model_id: str, policy: PrivacyPolicy) -> bool:
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
                if isinstance(model_body, dict) and isinstance(model_body.get("provider"), dict):
                    effective.update(model_body["provider"])
    else:
        providers_v1 = config.get("provider", {})
        openrouter = providers_v1.get("openrouter", {}) if isinstance(providers_v1, dict) else {}
        models = openrouter.get("models", {}) if isinstance(openrouter, dict) else {}
        model = models.get(model_id, {}) if isinstance(models, dict) else {}
        options = model.get("options", {}) if isinstance(model, dict) else {}
        if isinstance(options, dict) and isinstance(options.get("provider"), dict):
            effective = dict(options["provider"])
    return (not policy.no_training or effective.get("data_collection") == "deny") and (not policy.zero_retention or effective.get("zdr") is True)


def _consent_env(role: str, route: str) -> bool:
    for item in os.environ.get("AUTODEV_PRIVACY_CONSENT", "").split(","):
        left, sep, right = item.strip().partition("=")
        if sep and left.strip() == role and right.strip() == route:
            return True
    return False


def _consent_or_block(repo: Path, policy: PrivacyPolicy, decision: PrivacyDecision, consent_reader: Callable[[str], str] | None) -> PrivacyDecision:
    if _consent_env(decision.role, decision.route):
        decision.outcome = "ALLOW"
        decision.enforcement_state = "user-consented"
        decision.consent_scope = "exact role+route via AUTODEV_PRIVACY_CONSENT"
        _audit(repo, decision)
        return decision

    reader = consent_reader
    if reader is None and policy.consent_mode == "explicit" and sys.stdin is not None and sys.stdin.isatty():
        reader = input
    if reader is not None and policy.consent_mode == "explicit":
        answer = str(reader(
            "AutoDev could not verify the required privacy policy before sending repository/run content.\n\n"
            f"Role: {decision.role}\nRoute: {decision.route}\n"
            f"Training: {decision.training}\nCustomer-content retention: {decision.retention}"
            + (f" ({decision.retention_duration})" if decision.retention_duration else "")
            + f"\nReason: {decision.reason}\n\nAllow this exact role+route for this run? [y/N] "
        ) or "").strip().casefold()
        if answer in {"y", "yes"}:
            decision.outcome = "ALLOW"
            decision.enforcement_state = "user-consented"
            decision.consent_scope = "this exact role+route for this run"
            _audit(repo, decision)
            return decision

    decision.outcome = "BLOCK"
    _audit(repo, decision)
    raise PrivacyError(
        f"privacy blocked {decision.role} route {decision.route}: {decision.reason}. "
        "Configure/verify a compliant privacy control or explicitly consent to this exact role+route."
    )


def _audit(repo: Path, decision: PrivacyDecision) -> None:
    current = repo / ".autodev-run" / "current"
    path = (current if current.exists() else repo / ".autodev-run") / PRIVACY_AUDIT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), **decision.safe_metadata()}, sort_keys=True) + "\n")
    except OSError:
        pass

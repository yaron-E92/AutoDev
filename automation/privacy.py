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
    provider_attestations: dict[str, dict[str, object]] = field(default_factory=dict)

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
    attestations: list[str] = field(default_factory=list)
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
            "attested_controls": list(self.attestations),
            "consent_scope": self.consent_scope,
            "reason": self.reason,
        }


# Reviewed policy facts are intentionally time-bounded. They are not permanent trust decisions.
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

    attestations: dict[str, dict[str, object]] = {}
    raw_attestations = values.get("provider_attestations", {})
    if raw_attestations not in ({}, None):
        if not isinstance(raw_attestations, dict):
            raise PrivacyError("provider_attestations must be an object")
        for provider, item in raw_attestations.items():
            if not isinstance(provider, str) or not provider.strip() or not isinstance(item, dict):
                raise PrivacyError("provider_attestations entries must be provider-name objects")
            attestations[provider.strip().casefold()] = dict(item)

    return PrivacyPolicy(
        profile=profile,
        consent_mode=consent_mode,
        source=str(config) if config.is_file() else "default",
        provider_attestations=attestations,
    )


def privacy_repo() -> Path:
    explicit = os.environ.get("AUTODEV_TARGET_REPO", "").strip()
    if explicit:
        return Path(explicit)
    values = list(sys.argv[1:])
    for index, value in enumerate(values):
        if value in {"--repo", "--working-directory"} and index + 1 < len(values):
            return Path(values[index + 1])
        for prefix in ("--repo=", "--working-directory="):
            if value.startswith(prefix):
                return Path(value.split("=", 1)[1])
    return Path.cwd()


def _stronger_profile(repo_profile: str, env_profile: str) -> str:
    rank = {"off": 0, "no-training": 1, "strict-confidential": 2, "local-only": 3}
    if repo_profile not in rank:
        raise PrivacyError(f"unsupported privacy profile: {repo_profile}")
    if env_profile not in rank:
        raise PrivacyError(f"unsupported AUTODEV_PRIVACY_PROFILE: {env_profile}")
    return env_profile if rank[env_profile] >= rank[repo_profile] else repo_profile


def _fresh_date(value: str, *, max_age_days: int = POLICY_TTL_DAYS) -> bool:
    try:
        checked = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    now = datetime.now(timezone.utc)
    return checked <= now <= checked + timedelta(days=max_age_days)


def _policy_fresh() -> bool:
    return _fresh_date(POLICY_REVIEWED_AT)


def _split_model(model: str) -> tuple[str, str]:
    provider, sep, model_id = (model or "").partition("/")
    return (provider.casefold(), model_id) if sep else ("", model)


def _ollama_cloud(model: str) -> bool:
    lowered = model.casefold()
    return ":cloud" in lowered or lowered.endswith("-cloud") or "/cloud/" in lowered


def _provider_id(config) -> str:
    model = str(getattr(config, "model", "") or "")
    base_url = str(getattr(config, "base_url", "") or "")
    host = (urlparse(base_url).hostname or "").casefold()
    command = str(getattr(config, "command", "") or "").casefold()

    # Transport endpoint is authoritative. A model ID such as openai/foo may still be routed by OpenRouter.
    if "openrouter.ai" in host:
        return "openrouter"
    if host.endswith("groq.com"):
        return "groq"
    if host == "api.openai.com" or host.endswith(".api.openai.com"):
        return "openai"
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "ollama-cloud" if _ollama_cloud(model) else "local"

    model_provider, _ = _split_model(model)
    if model_provider == "ollama":
        return "ollama-cloud" if _ollama_cloud(model) else "local"
    if model_provider in {"openrouter", "groq", "openai"}:
        return model_provider
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
    if not _policy_fresh() or provider not in _PROVIDER_POLICY:
        return "unknown", "unknown", "", "unknown or stale policy"
    return _PROVIDER_POLICY[provider]


def _attestation(policy: PrivacyPolicy, provider: str) -> dict[str, object]:
    value = policy.provider_attestations.get(provider.casefold(), {})
    if not value:
        return {}
    if not _fresh_date(str(value.get("checked_at", ""))):
        return {}
    return value


def _apply_attestation(policy: PrivacyPolicy, decision: PrivacyDecision) -> None:
    value = _attestation(policy, decision.provider)
    if not value:
        return

    if decision.provider == "openrouter":
        if str(value.get("use_inputs_outputs", "")).casefold() == "disabled":
            decision.training = "denied"
            decision.attestations.append("openrouter.use_inputs_outputs=disabled")
        if str(value.get("prompt_logging", "")).casefold() == "disabled":
            decision.retention = "zero"
            decision.retention_duration = ""
            decision.attestations.append("openrouter.prompt_logging=disabled")
    elif decision.provider in {"groq", "openai"}:
        if str(value.get("zero_data_retention", "")).casefold() == "enabled":
            decision.retention = "zero"
            decision.retention_duration = ""
            decision.attestations.append(f"{decision.provider}.zero_data_retention=enabled")
    elif decision.provider == "openai-opencode":
        if str(value.get("training_on_customer_content", "")).casefold() == "denied":
            decision.training = "denied"
            decision.attestations.append("openai-opencode.training_on_customer_content=denied")
        if str(value.get("zero_data_retention", "")).casefold() == "enabled":
            decision.retention = "zero"
            decision.retention_duration = ""
            decision.attestations.append("openai-opencode.zero_data_retention=enabled")

    if decision.attestations:
        suffix = "account-attested" if decision.enforcement_state == "unverified" else "+account-attested"
        decision.enforcement_state = (
            suffix.lstrip("+") if decision.enforcement_state == "unverified" else decision.enforcement_state + suffix
        )


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
    return (
        isinstance(provider, dict)
        and (not policy.no_training or provider.get("data_collection") == "deny")
        and (not policy.zero_retention or provider.get("zdr") is True)
    )


def _block(repo: Path, decision: PrivacyDecision) -> PrivacyDecision:
    decision.outcome = "BLOCK"
    _audit(repo, decision)
    raise PrivacyError(
        f"privacy blocked {decision.role} route {decision.route}: {decision.reason}. "
        "Configure/verify a compliant privacy control or explicitly consent to this exact role+route."
    )


def authorize_direct_call(
    provider,
    config,
    *,
    role: str,
    repo: Path | None = None,
    consent_reader: Callable[[str], str] | None = None,
) -> PrivacyDecision:
    repo = (repo or privacy_repo()).expanduser().resolve()
    policy = load_policy(repo)
    provider_id = _provider_id(config)
    model = str(getattr(config, "model", "") or "")
    route = f"{provider_id}/{model}" if model else provider_id
    if not policy.enabled:
        decision = PrivacyDecision(
            "ALLOW", role, route, provider_id, model, _scope(provider_id),
            enforcement_state="not-required", reason="privacy policy disabled",
        )
        _audit(repo, decision)
        return decision

    if policy.local_only and provider_id != "local":
        return _block(
            repo,
            PrivacyDecision(
                "BLOCK", role, route, provider_id, model, _scope(provider_id),
                reason="repository privacy profile is local-only; cloud exceptions are forbidden",
            ),
        )

    if provider_id == "openrouter":
        option_sets = _request_option_sets(provider)
        controls: list[str] = []
        if option_sets:
            for options in option_sets:
                controls.extend(_merge_openrouter(options, policy))
        request_verified = bool(option_sets) and all(
            _openrouter_verified(options, policy) for options in option_sets
        )
        decision = PrivacyDecision(
            "ALLOW", role, route, provider_id, model, "routed-cloud",
            training="unknown",
            retention="unknown",
            policy_source=(
                "https://openrouter.ai/docs/guides/routing/provider-selection; "
                "https://openrouter.ai/docs/guides/privacy/data-collection"
            ),
            enforcement_state="request-verified" if request_verified else "unverified",
            controls=sorted(set(controls)),
            reason=(
                "OpenRouter downstream data-policy controls verified, but OpenRouter account-level "
                "content logging/data-sharing settings must also be verified or freshly attested"
            ),
        )
        _apply_attestation(policy, decision)
        if request_verified and _satisfies(policy, decision):
            _audit(repo, decision)
            return decision
        decision.outcome = "CONSENT_REQUIRED"
        decision.reason = _gap(policy, decision)
        return _authorize_evaluated_decision(repo, decision, consent_reader)

    training, retention, duration, source = _classify(provider_id)
    decision = PrivacyDecision(
        "ALLOW", role, route, provider_id, model, _scope(provider_id),
        training, retention, duration, policy_source=source,
        enforcement_state="verified-effective" if provider_id == "local" else "enforced-by-provider-contract",
    )
    _apply_attestation(policy, decision)
    if _satisfies(policy, decision):
        _audit(repo, decision)
        return decision
    decision.outcome = "CONSENT_REQUIRED"
    decision.reason = _gap(policy, decision)
    return _authorize_evaluated_decision(repo, decision, consent_reader)


def _authorize_evaluated_decision(
    repo: Path,
    decision: PrivacyDecision,
    consent_reader: Callable[[str], str] | None,
) -> PrivacyDecision:
    from automation import privacy_authorization

    return privacy_authorization.authorize_evaluated(
        repo,
        decision,
        consent_reader=consent_reader,
    )


def _deep_merge(base: dict[str, object], overlay: dict[str, object]) -> dict[str, object]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)  # type: ignore[arg-type]
        else:
            result[key] = value
    return result


def _consent_env(role: str, route: str) -> bool:
    for item in os.environ.get("AUTODEV_PRIVACY_CONSENT", "").split(","):
        left, sep, right = item.strip().partition("=")
        if sep and left.strip() == role and right.strip() == route:
            return True
    return False


def _consent_or_block(
    repo: Path,
    policy: PrivacyPolicy,
    decision: PrivacyDecision,
    consent_reader: Callable[[str], str] | None,
) -> PrivacyDecision:
    if policy.local_only:
        return _block(repo, decision)

    if _consent_env(decision.role, decision.route):
        decision.outcome = "ALLOW"
        decision.enforcement_state = "user-consented"
        decision.consent_scope = "exact role+route via AUTODEV_PRIVACY_CONSENT"
        _audit(repo, decision)
        return decision

    reader = consent_reader
    if (
        reader is None
        and policy.consent_mode == "explicit"
        and sys.stdin is not None
        and sys.stdin.isatty()
    ):
        reader = input
    if reader is not None and policy.consent_mode == "explicit":
        answer = str(
            reader(
                "AutoDev could not verify the required privacy policy before sending repository/run content.\n\n"
                f"Role: {decision.role}\nRoute: {decision.route}\n"
                f"Training: {decision.training}\nCustomer-content retention: {decision.retention}"
                + (f" ({decision.retention_duration})" if decision.retention_duration else "")
                + f"\nReason: {decision.reason}\n\nAllow this exact role+route for this call? [y/N] "
            )
            or ""
        ).strip().casefold()
        if answer in {"y", "yes"}:
            decision.outcome = "ALLOW"
            decision.enforcement_state = "user-consented"
            decision.consent_scope = "this call"
            _audit(repo, decision)
            return decision

    return _block(repo, decision)


def _audit(repo: Path, decision: PrivacyDecision) -> None:
    current = repo / ".autodev-run" / "current"
    path = (current if current.exists() else repo / ".autodev-run") / PRIVACY_AUDIT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        **decision.safe_metadata(),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
    except OSError:
        # Privacy enforcement must never become weaker because audit persistence failed.
        pass

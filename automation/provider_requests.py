from __future__ import annotations

from typing import Any

from automation.provider_contract import (
    ProviderError,
    SAFE_HEADER_NAMES,
    SENSITIVE_HEADER_NAMES,
)

def build_chat_completions_body(
    model: str,
    prompt: str,
    *,
    request_options: dict[str, object] | None = None,
    output_limit: int | None = None,
    free_only: bool = False,
    fallback_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    body = validated_request_options(request_options, reserved={"model", "models", "messages", "max_tokens"})
    apply_model_selection(body, model, fallback_models, free_only)
    body["messages"] = [{"role": "user", "content": prompt}]
    if output_limit is not None:
        body["max_tokens"] = validate_output_limit(output_limit)
    apply_free_only_routing(body, free_only)
    return body

def build_responses_body(
    model: str,
    prompt: str,
    *,
    request_options: dict[str, object] | None = None,
    output_limit: int | None = None,
    free_only: bool = False,
    fallback_models: tuple[str, ...] = (),
) -> dict[str, Any]:
    body = validated_request_options(request_options, reserved={"model", "models", "input", "max_output_tokens"})
    apply_model_selection(body, model, fallback_models, free_only)
    body["input"] = prompt
    if output_limit is not None:
        body["max_output_tokens"] = validate_output_limit(output_limit)
    apply_free_only_routing(body, free_only)
    return body

def validated_request_options(
    values: dict[str, object] | None,
    *,
    reserved: set[str],
) -> dict[str, Any]:
    options = dict(values or {})
    conflicts = sorted(reserved.intersection(options))
    if conflicts:
        raise ProviderError(
            "request_options cannot override: " + ", ".join(conflicts),
            classification="invalid_config",
        )
    return options

def apply_model_selection(
    body: dict[str, Any],
    model: str,
    fallback_models: tuple[str, ...],
    free_only: bool,
) -> None:
    models = (model, *fallback_models)
    if free_only and any(not item.endswith(":free") for item in models):
        raise ProviderError(
            "free-only configuration requires every model and fallback to end with :free",
            classification="free_only_violation",
        )
    if fallback_models:
        body["models"] = list(models)
    else:
        body["model"] = model

def apply_free_only_routing(body: dict[str, Any], free_only: bool) -> None:
    if not free_only:
        return
    provider = body.get("provider", {})
    if not isinstance(provider, dict):
        raise ProviderError("request_options.provider must be an object", classification="invalid_config")
    provider = dict(provider)
    provider["allow_fallbacks"] = False
    body["provider"] = provider

def validate_output_limit(value: int) -> int:
    if value <= 0:
        raise ProviderError("output_limit must be greater than zero", classification="invalid_config")
    return value

def validate_safe_headers(values: dict[str, str]) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in values.items():
        normalized = str(name).strip().casefold()
        if normalized in SENSITIVE_HEADER_NAMES or normalized not in SAFE_HEADER_NAMES:
            raise ProviderError(f"header is not allowlisted: {name}", classification="invalid_config")
        safe[str(name)] = str(value)
    return safe

def classify_http_status(status_code: int) -> str:
    return {
        401: "authentication_failed",
        402: "payment_required",
        404: "not_found",
        408: "timeout",
        429: "rate_limited",
    }.get(status_code, "http_error")

def http_failure_message(status_code: int) -> str:
    return {
        401: "provider authentication failed (HTTP 401)",
        402: "provider payment or plan is required (HTTP 402)",
        404: "provider endpoint or model was not found (HTTP 404)",
        408: "provider request timed out (HTTP 408)",
        429: "provider rate limit or quota was exhausted (HTTP 429)",
    }.get(status_code, f"provider request failed (HTTP {status_code})")

def response_telemetry(payload: dict[str, object]) -> dict[str, object]:
    telemetry: dict[str, object] = {}
    reported_model = payload.get("model")
    if isinstance(reported_model, str):
        telemetry["reported_model"] = reported_model
    usage = payload.get("usage")
    if isinstance(usage, dict):
        safe_usage = {
            str(key): value
            for key, value in usage.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if safe_usage:
            telemetry["usage"] = safe_usage
        cost = usage.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            telemetry["reported_cost"] = cost
    cost = payload.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        telemetry["reported_cost"] = cost
    return telemetry

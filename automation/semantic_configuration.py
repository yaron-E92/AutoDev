from __future__ import annotations


from automation.semantic_contract import (
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_MAX_SCHEMA_RETRIES,
    MAX_REPAIR_ATTEMPTS,
    MAX_SCHEMA_RETRIES,
    SemanticSettings,
    SemanticVerifierError,
)

def resolve_semantic_settings(
    file_config: dict[str, object],
    *,
    verifier_configured: bool,
) -> SemanticSettings:
    value = file_config.get("semantic_verification", {})
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise _config_error("semantic_verification must be an object")

    enabled = value.get("enabled", verifier_configured)
    if not isinstance(enabled, bool):
        raise _config_error("semantic_verification.enabled must be boolean")

    max_schema_retries = _bounded_count(
        value.get("max_schema_retries", DEFAULT_MAX_SCHEMA_RETRIES),
        "semantic_verification.max_schema_retries",
        MAX_SCHEMA_RETRIES,
    )
    max_repair_attempts = _bounded_count(
        value.get("max_repair_attempts", DEFAULT_MAX_REPAIR_ATTEMPTS),
        "semantic_verification.max_repair_attempts",
        MAX_REPAIR_ATTEMPTS,
    )
    if enabled and not verifier_configured:
        raise _config_error(
            "semantic verification is enabled but the verifier role is not configured"
        )
    return SemanticSettings(enabled, max_schema_retries, max_repair_attempts)

def safe_semantic_metadata(settings: SemanticSettings) -> dict[str, object]:
    return {
        "enabled": settings.enabled,
        "max_schema_retries": settings.max_schema_retries,
        "max_repair_attempts": settings.max_repair_attempts,
    }

def _bounded_count(value: object, label: str, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise _config_error(f"{label} must be an integer") from exc
    if parsed < 0 or parsed > maximum:
        raise _config_error(f"{label} must be between 0 and {maximum}")
    return parsed

def _config_error(message: str) -> SemanticVerifierError:
    return SemanticVerifierError(message, classification="invalid_config")

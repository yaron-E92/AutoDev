from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from automation.evaluation_contract import (
    EvalError,
    REPO_ROOT,
    SCHEMA_VERSION,
    UNKNOWN,
)

def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EvalError(f"evaluation file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EvalError(f"evaluation JSON is invalid: {path}") from exc

def load_cases(path: Path) -> dict[str, dict[str, object]]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise EvalError(f"unsupported evaluation case schema in {path}")
    raw = value.get("cases")
    if not isinstance(raw, list) or not raw:
        raise EvalError("evaluation cases must be a non-empty array")
    cases: dict[str, dict[str, object]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise EvalError("each evaluation case must be an object")
        case_id = str(item.get("id", "")).strip()
        if not case_id or case_id in cases:
            raise EvalError(f"evaluation case id is missing or duplicated: {case_id or '<empty>'}")
        if not str(item.get("issue_text", "")).strip():
            raise EvalError(f"evaluation case {case_id} has no issue_text")
        source = item.get("source")
        if not isinstance(source, dict) or source.get("kind") not in {"replay", "fixture", "public"}:
            raise EvalError(f"evaluation case {case_id} has an invalid source")
        if not isinstance(item.get("expected"), dict):
            raise EvalError(f"evaluation case {case_id} has no expected object")
        cases[case_id] = item
    return cases

def load_profiles(path: Path, *, repo_root: Path = REPO_ROOT) -> dict[str, dict[str, object]]:
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise EvalError(f"unsupported evaluation profile schema in {path}")
    raw = value.get("profiles")
    if not isinstance(raw, dict) or not raw:
        raise EvalError("evaluation profiles must be a non-empty object")
    profiles: dict[str, dict[str, object]] = {}
    for name, item in raw.items():
        if not isinstance(item, dict):
            raise EvalError(f"evaluation profile {name} must be an object")
        provider_value = str(item.get("provider_config", "")).strip()
        if not provider_value:
            raise EvalError(f"evaluation profile {name} has no provider_config")
        provider_path = (repo_root / provider_value).resolve()
        provider = read_json(provider_path)
        if not isinstance(provider, dict):
            raise EvalError(f"evaluation profile {name} provider config must be an object")
        summary = safe_provider_summary(provider)
        ensure_free_route_safety(str(name), summary)
        profiles[str(name)] = {
            **item,
            "name": str(name),
            "provider_path": str(provider_path),
            "provider_summary": summary,
            "fingerprint": fingerprint(
                {
                    "provider_config": provider_value,
                    "provider_summary": summary,
                    "evaluation": item.get("evaluation", {}),
                }
            ),
        }
    return profiles

def safe_provider_summary(config: dict[str, object]) -> dict[str, object]:
    roles_value = config.get("roles")
    roles = roles_value if isinstance(roles_value, dict) else {
        role: config.get(role, {})
        for role in ("reader", "coder")
        if isinstance(config.get(role), dict)
    }
    safe_roles: dict[str, object] = {}
    for role, raw in roles.items():
        if not isinstance(raw, dict):
            continue
        safe_roles[str(role)] = {
            "transport": str(raw.get("transport") or raw.get("provider") or ""),
            "model": str(raw.get("model") or ""),
            "profile_name": str(raw.get("profile_name") or ""),
            "endpoint": sanitized_url(raw.get("base_url", "")),
            "timeout_seconds": raw.get("timeout_seconds", UNKNOWN),
            "free_only": bool(raw.get("free_only", False)),
            "api_key_env": str(raw.get("api_key_env") or ""),
            "fallbacks": safe_fallbacks(raw),
        }
    return {
        "name": str(config.get("name") or ""),
        "version": config.get("version", UNKNOWN),
        "roles": safe_roles,
        "prompt_policy": redact(config.get("prompt_policy", {})),
        "semantic_verification": redact(config.get("semantic_verification", {})),
        "headroom": safe_headroom(config.get("headroom", {})),
    }

def safe_fallbacks(role_config: dict[str, object]) -> list[str]:
    value = role_config.get("fallbacks", role_config.get("fallback_models", []))
    return [str(item) for item in value] if isinstance(value, list) else []

def safe_headroom(value: object) -> object:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if key in {"enabled", "mode", "output_shaping", "fail_open", "roles", "version"}
    }

def redact(value: object, key: str = "") -> object:
    lowered = key.casefold().replace("-", "_")
    if any(token in lowered for token in ("api_key", "authorization", "password", "secret", "token", "cookie")):
        return "<redacted>"
    if lowered == "headers":
        return {str(item_key): "<redacted>" for item_key in value} if isinstance(value, dict) else "<redacted>"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    return value

def sanitized_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return "configured"
    hostname = parts.hostname or ""
    if parts.port is not None:
        hostname += f":{parts.port}"
    return urlunsplit((parts.scheme, hostname, parts.path, "", ""))

def ensure_free_route_safety(profile_name: str, summary: dict[str, object]) -> None:
    roles = summary.get("roles", {})
    if not isinstance(roles, dict):
        return
    for role, raw in roles.items():
        if not isinstance(raw, dict):
            continue
        model = str(raw.get("model", ""))
        endpoint = str(raw.get("endpoint", "")).casefold()
        if "openrouter.ai" not in endpoint or not (model.endswith(":free") or raw.get("free_only")):
            continue
        if not model.endswith(":free") or not bool(raw.get("free_only")):
            raise EvalError(
                f"profile {profile_name} role {role} is an OpenRouter free comparison without both :free and free_only=true"
            )
        paid = [item for item in raw.get("fallbacks", []) if not str(item).endswith(":free")]
        if paid:
            raise EvalError(f"profile {profile_name} role {role} permits a paid fallback: {', '.join(paid)}")

def fingerprint(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()

def selected_cases(
    all_cases: dict[str, dict[str, object]],
    requested: list[str],
    tags: list[str],
) -> list[dict[str, object]]:
    values = [all_cases[name] for name in requested] if requested else list(all_cases.values())
    if tags:
        wanted = set(tags)
        values = [case for case in values if wanted.intersection(str(tag) for tag in case.get("tags", []))]
    return values

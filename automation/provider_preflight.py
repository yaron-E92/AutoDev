from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from automation.model_providers import ModelConfig, ProviderError, classify_http_status, load_provider_config, ollama_command_for_model
from automation.model_roles import MODEL_ROLES, resolve_role_configs, safe_role_metadata


DEFAULT_READER_MODEL = "qwen35-9b-32k"
DEFAULT_CODER_MODEL = "devstral-small2-12k"


def resolve_profile(path: Path) -> tuple[dict[str, object], dict[str, ModelConfig | None]]:
    file_config = load_provider_config(str(path))
    defaults = {
        "reader": {
            "provider": "command",
            "model": DEFAULT_READER_MODEL,
            "command": ollama_command_for_model(DEFAULT_READER_MODEL),
        },
        "coder": {
            "provider": "command",
            "model": DEFAULT_CODER_MODEL,
            "command": ollama_command_for_model(DEFAULT_CODER_MODEL),
        },
    }
    return file_config, resolve_role_configs(defaults=defaults, file_config=file_config)


def command_executable(config: ModelConfig) -> str:
    try:
        argv = shlex.split(config.command, posix=os.name != "nt")
    except ValueError as exc:
        raise ProviderError("command provider command is malformed", classification="invalid_config") from exc
    if not argv:
        raise ProviderError("command provider command is empty", classification="invalid_config")
    return argv[0]


def models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


def check_http_config(
    config: ModelConfig,
    *,
    urlopen=request.urlopen,
) -> dict[str, object]:
    if config.api_key_env and not os.environ.get(config.api_key_env):
        return {
            "status": "failure",
            "failure_classification": "missing_credentials",
            "message": f"Set environment variable {config.api_key_env}.",
        }

    headers = {"Accept": "application/json", **config.headers}
    if config.api_key_env:
        headers["Authorization"] = f"Bearer {os.environ[config.api_key_env]}"
    req = request.Request(models_url(config.base_url), headers=headers, method="GET")
    try:
        with urlopen(req, timeout=min(config.timeout_seconds, 30)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        return {
            "status": "failure",
            "failure_classification": classify_http_status(exc.code),
            "status_code": exc.code,
            "message": f"Provider model check failed with HTTP {exc.code}.",
        }
    except (error.URLError, TimeoutError):
        return {
            "status": "failure",
            "failure_classification": "transport_error",
            "message": "Provider endpoint is unreachable.",
        }
    except json.JSONDecodeError:
        return {
            "status": "failure",
            "failure_classification": "malformed_response",
            "message": "Provider model endpoint returned malformed JSON.",
        }

    identifiers: set[str] = set()
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        for item in payload["data"]:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                identifiers.add(item["id"])
    if identifiers and config.model not in identifiers:
        return {
            "status": "failure",
            "failure_classification": "model_not_found",
            "message": f"Configured model is not listed: {config.model}",
        }
    return {"status": "success", "message": "Endpoint and credentials are reachable."}


def check_config(
    config: ModelConfig,
    *,
    which=shutil.which,
    urlopen=request.urlopen,
) -> dict[str, object]:
    if config.provider == "command":
        executable = command_executable(config)
        if which(executable) is None:
            return {
                "status": "failure",
                "failure_classification": "command_unavailable",
                "message": f"Command executable is not available: {executable}",
            }
        return {"status": "success", "message": f"Command executable is available: {executable}"}
    if config.provider.startswith("openai-compatible-"):
        return check_http_config(config, urlopen=urlopen)
    if config.provider == "mock":
        return {"status": "success", "message": "Mock transport requires no external preflight."}
    return {
        "status": "failure",
        "failure_classification": "invalid_config",
        "message": f"Unsupported transport: {config.provider}",
    }


def run_preflight(
    profile_path: Path,
    *,
    which=shutil.which,
    urlopen=request.urlopen,
) -> dict[str, object]:
    profile_path = profile_path.expanduser().resolve()
    result: dict[str, object] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "profile": {"name": profile_path.name, "path": str(profile_path)},
        "status": "failure",
        "roles": {},
        "checks": [],
    }
    try:
        file_config, configs = resolve_profile(profile_path)
        result["profile"]["configured_name"] = str(file_config.get("name", ""))
        result["roles"] = safe_role_metadata(configs)
    except (OSError, json.JSONDecodeError, ProviderError, ValueError) as exc:
        result["failure_classification"] = "invalid_config"
        result["message"] = str(exc)
        result["ended_at"] = datetime.now(timezone.utc).isoformat()
        return result

    checked: dict[tuple[object, ...], dict[str, object]] = {}
    checks: list[dict[str, object]] = []
    for role in MODEL_ROLES:
        config = configs.get(role)
        if config is None:
            checks.append({"role": role, "status": "disabled"})
            continue
        key = (
            config.provider,
            config.model,
            config.command,
            config.base_url,
            config.api_key_env,
        )
        check = checked.get(key)
        if check is None:
            check = check_config(config, which=which, urlopen=urlopen)
            checked[key] = check
        checks.append({"role": role, "transport": config.provider, "model": config.model, **check})

    result["checks"] = checks
    failures = [item for item in checks if item.get("status") == "failure"]
    if failures:
        result["failure_classification"] = failures[0].get("failure_classification", "provider_error")
        result["message"] = failures[0].get("message", "Provider preflight failed.")
    else:
        result["status"] = "success"
        result["message"] = "Provider preflight passed."
    result["ended_at"] = datetime.now(timezone.utc).isoformat()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an AutoDev provider profile without mutating a repository or GitHub.")
    parser.add_argument("--provider-profile", "--provider-config", dest="provider_profile", required=True)
    parser.add_argument("--out", default="provider-preflight.json")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_preflight(Path(args.provider_profile))
    output = Path(args.out).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{result['status']}: {result['message']}")
    print(f"Result: {output}")
    return 0 if result["status"] == "success" else 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

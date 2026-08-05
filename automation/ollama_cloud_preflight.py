from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

from automation.model_providers import ModelConfig, ProviderError, load_provider_config
from automation.model_roles import MODEL_ROLES, resolve_role_configs


MIN_OLLAMA_VERSION = (0, 12, 0)
DEFAULT_API_URL = "http://localhost:11434/api/tags"
DEFAULT_PULL_TIMEOUT_SECONDS = 300

_UPGRADE_MARKERS = (
    "upgrade required",
    "plan upgrade",
    "requires an ollama plan",
    "subscription required",
    "insufficient plan",
    "payment required",
    "status code 402",
    "http 402",
)
_SIGNIN_MARKERS = (
    "ollama signin",
    "sign in",
    "signin required",
    "not signed in",
    "authentication required",
    "requires authentication",
    "unauthorized",
    "status code 401",
    "http 401",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an Ollama Cloud role profile without touching a target repository."
    )
    parser.add_argument("--profile", required=True, help="Version-2 provider profile JSON.")
    parser.add_argument(
        "--out",
        default="ollama-cloud-preflight.json",
        help="JSON result path. Default: ollama-cloud-preflight.json",
    )
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help=f"Local Ollama tags endpoint. Default: {DEFAULT_API_URL}",
    )
    parser.add_argument(
        "--pull-timeout-seconds",
        type=positive_int,
        default=DEFAULT_PULL_TIMEOUT_SECONDS,
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def parse_ollama_version(value: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)", value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def load_cloud_profile(path: Path) -> dict[str, ModelConfig]:
    file_config = load_provider_config(str(path))
    if file_config.get("version") != 2:
        raise ProviderError("Ollama Cloud preflight requires a version 2 provider profile")

    role_sections = file_config.get("roles")
    if not isinstance(role_sections, dict):
        raise ProviderError("provider config version 2 requires a roles object")

    missing = [role for role in MODEL_ROLES if role not in role_sections]
    if missing:
        raise ProviderError("cloud profile is missing role(s): " + ", ".join(missing))

    defaults = {
        "reader": {
            "provider": "command",
            "model": "unused-reader",
            "command": "ollama run unused-reader",
        },
        "coder": {
            "provider": "command",
            "model": "unused-coder",
            "command": "ollama run unused-coder",
        },
    }
    resolved = resolve_role_configs(
        defaults=defaults,
        file_config=file_config,
    )

    configs: dict[str, ModelConfig] = {}
    for role in MODEL_ROLES:
        config = resolved[role]
        if config is None:
            raise ProviderError(f"cloud profile role is disabled: {role}")
        configs[role] = config
    return configs


def classify_pull_failure(output: str) -> str:
    lowered = output.casefold()
    if any(marker in lowered for marker in _UPGRADE_MARKERS):
        return "upgrade_required"
    if any(marker in lowered for marker in _SIGNIN_MARKERS):
        return "signin_required"
    return "model_failure"


def failure_message(failure_type: str, model: str = "") -> str:
    if failure_type == "missing_ollama":
        return "Ollama is not installed or is not available on PATH."
    if failure_type == "outdated_ollama":
        return f"Ollama {format_version(MIN_OLLAMA_VERSION)} or newer is required for cloud models."
    if failure_type == "version_unrecognized":
        return "The installed Ollama version could not be determined."
    if failure_type == "service_unreachable":
        return "The local Ollama service is not reachable."
    if failure_type == "signin_required":
        return f"Sign in with `ollama signin` before accessing {model}."
    if failure_type == "upgrade_required":
        return f"{model} requires an Ollama plan upgrade."
    return f"Could not access {model} with `ollama pull`."


def _base_result(profile_path: Path, api_url: str) -> dict[str, object]:
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "profile": {
            "name": profile_path.name,
            "path": str(profile_path),
        },
        "minimum_ollama_version": format_version(MIN_OLLAMA_VERSION),
        "ollama_version": "",
        "roles": {},
        "service": {
            "url": api_url,
            "status": "not_checked",
        },
        "models": [],
        "status": "failure",
        "failure_type": "",
        "message": "",
    }


def _finish(result: dict[str, object]) -> dict[str, object]:
    result["ended_at"] = datetime.now(timezone.utc).isoformat()
    return result


def run_preflight(
    profile_path: Path,
    *,
    api_url: str = DEFAULT_API_URL,
    pull_timeout_seconds: int = DEFAULT_PULL_TIMEOUT_SECONDS,
    which=shutil.which,
    run_command=subprocess.run,
    urlopen=request.urlopen,
) -> dict[str, object]:
    profile_path = profile_path.expanduser().resolve()
    result = _base_result(profile_path, api_url)

    try:
        configs = load_cloud_profile(profile_path)
    except (OSError, json.JSONDecodeError, ProviderError, ValueError) as exc:
        result["failure_type"] = "invalid_profile"
        result["message"] = str(exc)
        return _finish(result)

    result["roles"] = {
        role: {
            "provider": configs[role].provider,
            "model": configs[role].model,
            "timeout_seconds": configs[role].timeout_seconds,
        }
        for role in MODEL_ROLES
    }

    ollama = which("ollama")
    if not ollama:
        result["failure_type"] = "missing_ollama"
        result["message"] = failure_message("missing_ollama")
        return _finish(result)

    try:
        version_process = run_command(
            [ollama, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        result["failure_type"] = "version_unrecognized"
        result["message"] = failure_message("version_unrecognized")
        return _finish(result)

    version_output = "\n".join(
        part for part in (version_process.stdout, version_process.stderr) if part
    )
    version = parse_ollama_version(version_output)
    if version is None:
        result["failure_type"] = "version_unrecognized"
        result["message"] = failure_message("version_unrecognized")
        return _finish(result)

    result["ollama_version"] = format_version(version)
    if version < MIN_OLLAMA_VERSION:
        result["failure_type"] = "outdated_ollama"
        result["message"] = failure_message("outdated_ollama")
        return _finish(result)

    try:
        with urlopen(api_url, timeout=5) as response:
            json.loads(response.read().decode("utf-8"))
    except (OSError, error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError):
        result["service"]["status"] = "unreachable"
        result["failure_type"] = "service_unreachable"
        result["message"] = failure_message("service_unreachable")
        return _finish(result)
    result["service"]["status"] = "reachable"

    unique_models = list(dict.fromkeys(configs[role].model for role in MODEL_ROLES))
    model_results: list[dict[str, object]] = []
    for model in unique_models:
        try:
            pull = run_command(
                [ollama, "pull", model],
                capture_output=True,
                text=True,
                check=False,
                timeout=pull_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            model_results.append(
                {
                    "model": model,
                    "status": "model_failure",
                    "message": f"`ollama pull {model}` timed out.",
                }
            )
            continue
        except OSError:
            model_results.append(
                {
                    "model": model,
                    "status": "model_failure",
                    "message": failure_message("model_failure", model),
                }
            )
            continue

        if pull.returncode == 0:
            model_results.append(
                {
                    "model": model,
                    "status": "accessible",
                    "message": "Model is accessible.",
                }
            )
            continue

        output = "\n".join(part for part in (pull.stdout, pull.stderr) if part)
        failure_type = classify_pull_failure(output)
        model_results.append(
            {
                "model": model,
                "status": failure_type,
                "message": failure_message(failure_type, model),
            }
        )

    result["models"] = model_results
    failed = [item for item in model_results if item["status"] != "accessible"]
    if failed:
        first = failed[0]
        result["failure_type"] = first["status"]
        result["message"] = first["message"]
        return _finish(result)

    result["status"] = "success"
    result["message"] = "Ollama Cloud profile preflight passed."
    return _finish(result)


def write_result(path: Path, result: dict[str, object]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_preflight(
        Path(args.profile),
        api_url=args.api_url,
        pull_timeout_seconds=args.pull_timeout_seconds,
    )
    output_path = Path(args.out)
    write_result(output_path, result)
    print(f"{result['status']}: {result['message']}")
    print(f"Result: {output_path.expanduser().resolve()}")
    if result["status"] == "success":
        return 0
    return 2 if result["failure_type"] == "invalid_profile" else 1


if __name__ == "__main__":
    raise SystemExit(main())

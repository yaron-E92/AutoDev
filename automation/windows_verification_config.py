from __future__ import annotations

import hashlib
from pathlib import Path

from automation.windows_verification_contract import (
    CONFIG_PATH,
    DEFAULT_CALLER_WORKFLOW,
    DEFAULT_TIMEOUT_SECONDS,
    SCHEMA_VERSION,
    WindowsVerificationError,
    _ACTIONS_NAME_PATTERN,
)
from automation.windows_verification_storage import (
    _read_json,
)

def parse_deferred_obligations(output: str) -> list[dict[str, str]]:
    obligations: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if not line.startswith("DEFERRED:"):
            continue
        message = line[len("DEFERRED:") :].strip()
        if not message:
            continue
        lowered = message.casefold()
        platform = (
            "windows"
            if any(token in lowered for token in ("windows", "winui", "-windows"))
            else "compatible-host"
        )
        digest = hashlib.sha256(f"{platform}|{message}".encode("utf-8", errors="replace")).hexdigest()[:16]
        if digest in seen:
            continue
        seen.add(digest)
        obligations.append(
            {
                "id": digest,
                "platform": platform,
                "message": message,
                "source": "local-check",
            }
        )
    return obligations

def load_config(repo: Path) -> dict[str, object] | None:
    path = repo.expanduser().resolve() / CONFIG_PATH
    if not path.is_file():
        return None
    value = _read_json(path)
    if not isinstance(value, dict):
        raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} must contain a JSON object")
    if value.get("version") != SCHEMA_VERSION:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} version must be {SCHEMA_VERSION}"
        )
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} enabled must be boolean")
    when = str(value.get("when", "deferred-windows")).strip().casefold()
    if when not in {"deferred-windows", "always"}:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} when must be deferred-windows or always"
        )
    workflow = str(value.get("workflow", DEFAULT_CALLER_WORKFLOW)).strip()
    if enabled and (not workflow or "/" in workflow or "\\" in workflow):
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} workflow must be a workflow filename such as {DEFAULT_CALLER_WORKFLOW}"
        )
    commands = value.get("commands", [])
    if not isinstance(commands, list):
        raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} commands must be an array")
    normalized_commands: list[dict[str, str]] = []
    names: set[str] = set()
    for index, item in enumerate(commands):
        if not isinstance(item, dict):
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} commands[{index}] must be an object"
            )
        name = str(item.get("name", "")).strip()
        command = str(item.get("command", "")).strip()
        if not name or not command:
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} commands[{index}] requires name and command"
            )
        if name in names:
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} contains duplicate command name {name!r}"
            )
        names.add(name)
        normalized_commands.append({"name": name, "command": command})
    if enabled and not normalized_commands:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} enabled Windows verification requires at least one command"
        )
    timeout = value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise WindowsVerificationError(
            f"{CONFIG_PATH.as_posix()} timeout_seconds must be a positive integer"
        )
    setup_value = value.get("setup")
    setup: dict[str, object] | None = None
    if setup_value is not None:
        if not isinstance(setup_value, dict):
            raise WindowsVerificationError(f"{CONFIG_PATH.as_posix()} setup must be an object")
        setup_name = str(setup_value.get("name", "Repository verification setup")).strip()
        setup_command = str(setup_value.get("command", "")).strip()
        if not setup_name or not setup_command:
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} setup requires a non-empty name and command"
            )
        secret_env_value = setup_value.get("secret_env", {})
        if not isinstance(secret_env_value, dict):
            raise WindowsVerificationError(
                f"{CONFIG_PATH.as_posix()} setup.secret_env must be an object"
            )
        secret_env: dict[str, str] = {}
        for environment_name, secret_name_value in secret_env_value.items():
            secret_name = str(secret_name_value).strip()
            if (
                not isinstance(environment_name, str)
                or not _ACTIONS_NAME_PATTERN.fullmatch(environment_name)
                or not _ACTIONS_NAME_PATTERN.fullmatch(secret_name)
            ):
                raise WindowsVerificationError(
                    f"{CONFIG_PATH.as_posix()} setup.secret_env must map valid environment variable names "
                    "to GitHub Actions secret names"
                )
            secret_env[environment_name] = secret_name
        setup = {
            "name": setup_name,
            "command": setup_command,
            "secret_env": secret_env,
        }
    return {
        "version": SCHEMA_VERSION,
        "enabled": enabled,
        "when": when,
        "workflow": workflow or DEFAULT_CALLER_WORKFLOW,
        "commands": normalized_commands,
        "setup": setup,
        "timeout_seconds": timeout,
    }

def validate_config(repo: Path) -> None:
    load_config(repo)

def safe_config_metadata(config: dict[str, object] | None) -> dict[str, object]:
    if not config:
        return {"configured": False}
    commands = config.get("commands", [])
    setup = config.get("setup")
    safe_setup = None
    if isinstance(setup, dict):
        secret_env = setup.get("secret_env", {})
        safe_setup = {
            "configured": True,
            "name": str(setup.get("name", "")),
            "secret_environment_names": sorted(secret_env) if isinstance(secret_env, dict) else [],
        }
    return {
        "configured": True,
        "enabled": bool(config.get("enabled", True)),
        "when": str(config.get("when", "deferred-windows")),
        "workflow": str(config.get("workflow", DEFAULT_CALLER_WORKFLOW)),
        "timeout_seconds": int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS),
        "command_names": [
            str(item.get("name", ""))
            for item in commands
            if isinstance(item, dict) and str(item.get("name", ""))
        ],
        "setup": safe_setup,
    }

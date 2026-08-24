from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from automation import workflow_stages

from automation.opencode_adapter_contract import (
    AUTODEV_AGENT_BY_ROLE,
    OPENCODE_ROLE_NAMES,
    OpenCodeAdapterError,
    _UNSUPPORTED_MODEL_OVERRIDE,
)

def issue_number_from_arguments(arguments: str) -> int:
    return workflow_stages.issue_number_from_arguments(arguments)

def reject_unsupported_model_overrides(arguments: str) -> None:
    if _UNSUPPORTED_MODEL_OVERRIDE.search(arguments or ""):
        raise OpenCodeAdapterError(
            "per-run OpenCode model overrides are not supported because OpenCode does not document "
            "per-Task child-model selection; configure agent.autodev-*.model in opencode.json/jsonc "
            "before starting the OpenCode session"
        )

def resolve_opencode_model_mappings(
    repo: Path,
    *,
    runner=subprocess.run,
    which=None,
) -> dict[str, dict[str, str]]:
    repo = repo.expanduser().resolve()
    if which is None:
        which = shutil.which if runner is subprocess.run else lambda command: command
    opencode_cli = which("opencode")
    if not opencode_cli:
        raise OpenCodeAdapterError(
            "OpenCode CLI was not found on PATH; model mapping introspection requires an installed `opencode` CLI"
        )
    try:
        completed = runner(
            [opencode_cli, "debug", "config"],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise OpenCodeAdapterError(
            f"OpenCode CLI resolved to {opencode_cli!r} but could not be launched: {exc}"
        ) from exc
    returncode = int(getattr(completed, "returncode", 1))
    if returncode != 0:
        stderr = str(getattr(completed, "stderr", "") or "").strip()
        detail = f": {stderr}" if stderr else ""
        raise OpenCodeAdapterError(
            f"opencode debug config failed with exit code {returncode}{detail}; "
            "fix OpenCode configuration before running AutoDev"
        )
    raw = str(getattr(completed, "stdout", "") or "").strip()
    try:
        config = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise OpenCodeAdapterError(
            "opencode debug config returned invalid JSON; AutoDev cannot safely resolve role models"
        ) from exc
    if not isinstance(config, dict):
        raise OpenCodeAdapterError(
            "opencode debug config returned an unexpected value; AutoDev cannot safely resolve role models"
        )
    return model_mappings_from_config(config)

def model_mappings_from_config(config: dict[str, object]) -> dict[str, dict[str, str]]:
    global_model = _configured_model(config.get("model"), "OpenCode global/default model")
    raw_agents = config.get("agent", {})
    if raw_agents is None:
        raw_agents = {}
    if not isinstance(raw_agents, dict):
        raise OpenCodeAdapterError("OpenCode resolved `agent` configuration must be an object")

    known_agents = set(AUTODEV_AGENT_BY_ROLE.values())
    for agent_name, value in raw_agents.items():
        name = str(agent_name)
        if not name.startswith("autodev-") or name in known_agents:
            continue
        if isinstance(value, dict) and "model" in value:
            raise OpenCodeAdapterError(
                f"unknown AutoDev OpenCode role mapping: agent.{name}.model"
            )

    explicit: dict[str, str] = {}
    for role, agent_name in AUTODEV_AGENT_BY_ROLE.items():
        value = raw_agents.get(agent_name, {})
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise OpenCodeAdapterError(f"OpenCode agent.{agent_name} configuration must be an object")
        if "model" in value:
            explicit[role] = _configured_model(
                value.get("model"),
                f"OpenCode agent.{agent_name}.model",
                required=True,
            )

    coordinator_model = explicit.get("coordinator", global_model)
    report: dict[str, dict[str, str]] = {}
    for role in OPENCODE_ROLE_NAMES:
        agent_name = AUTODEV_AGENT_BY_ROLE[role]
        if role in explicit:
            report[role] = {
                "agent": agent_name,
                "source": "explicit",
                "model": explicit[role],
                "inherits_from": "",
            }
            continue
        if role == "coordinator":
            report[role] = {
                "agent": agent_name,
                "source": "inherited",
                "model": global_model,
                "inherits_from": (
                    "OpenCode global/default model"
                    if global_model
                    else "OpenCode current/default model (runtime /models selection may apply)"
                ),
            }
            continue
        report[role] = {
            "agent": agent_name,
            "source": "inherited",
            "model": coordinator_model,
            "inherits_from": (
                "autodev-coordinator during /autodev-issue-to-pr; invoking primary for standalone role commands"
            ),
        }
    return report

def _configured_model(value: object, label: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise OpenCodeAdapterError(f"{label} must be a non-empty provider/model identifier")
    model = value.strip()
    if any(character.isspace() for character in model):
        raise OpenCodeAdapterError(f"{label} must be a provider/model identifier without whitespace")
    provider, separator, model_id = model.partition("/")
    if not separator or not provider or not model_id or model_id.startswith("/"):
        raise OpenCodeAdapterError(f"{label} must use provider/model syntax")
    return model

def render_model_mappings(mappings: dict[str, dict[str, str]]) -> str:
    lines = ["AutoDev OpenCode role models:"]
    for role in OPENCODE_ROLE_NAMES:
        value = mappings[role]
        model = value.get("model", "")
        if value.get("source") == "explicit":
            resolution = f"{model} (explicit)"
        elif model:
            resolution = f"{model} (inherited from {value.get('inherits_from', '')})"
        else:
            resolution = f"inherited from {value.get('inherits_from', '')}"
        lines.append(f"{role:<13} {resolution}")
    return "\n".join(lines)

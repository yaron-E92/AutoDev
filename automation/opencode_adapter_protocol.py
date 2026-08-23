from __future__ import annotations

import json
import subprocess
from pathlib import Path
from automation import workflow_stages
from automation.model_providers import ProviderError, load_provider_config
from automation.prompt_policies import compose_prompt, resolve_prompt_policies

from automation.opencode_adapter_contract import (
    OPENCODE_PROTOCOL_VERSION,
    OpenCodeAdapterError,
    ROLE_NAMES,
    role_contracts,
)
from automation.opencode_adapter_storage import (
    _file_sha256,
    _read_diagnostics,
    _read_json,
    _read_state,
    _write_diagnostics,
    _write_json,
)

def ensure_current_issue(
    repo: Path,
    autodev_root: Path,
    arguments: str,
    *,
    runner=subprocess.run,
) -> Path:
    try:
        return workflow_stages.ensure_prepared_issue(
            repo.expanduser().resolve(),
            arguments,
            autodev_root=autodev_root.expanduser().resolve(),
            runner=runner,
        )
    except workflow_stages.WorkflowStageError as exc:
        raise OpenCodeAdapterError(
            str(exc),
            classification=exc.classification,
        ) from exc

def _ensure_opencode_protocol(current: Path) -> None:
    state = _read_state(current)
    state["OpenCodeProtocolVersion"] = OPENCODE_PROTOCOL_VERSION
    if not isinstance(state.get("AcceptedRoleArtifacts"), dict):
        state["AcceptedRoleArtifacts"] = {}
    _write_json(current / "state.json", state)
    _write_role_contracts(current)
    diagnostics = _read_diagnostics(current)
    diagnostics.setdefault("role_invocations", {})
    diagnostics.setdefault("protocol_correction_attempts", {})
    diagnostics.setdefault("protocol_correction_used", {})
    diagnostics.setdefault("stage_invocations", {})
    diagnostics.setdefault("stage_wall_time_ms", {})
    diagnostics.setdefault("repeated_identical_failures", 0)
    _write_diagnostics(current, diagnostics)

def _write_role_contracts(current: Path) -> None:
    _write_json(
        current / "role-contracts.json",
        {
            "version": OPENCODE_PROTOCOL_VERSION,
            "roles": role_contracts(),
            "protocol_correction_limit": 1,
        },
    )

def _begin_role_invocation(current: Path, role: str) -> None:
    if role not in ROLE_NAMES:
        raise OpenCodeAdapterError(f"unsupported OpenCode role: {role}")
    state = _read_state(current)
    accepted = state.get("AcceptedRoleArtifacts", {})
    if isinstance(accepted, dict):
        accepted.pop(role, None)
        state["AcceptedRoleArtifacts"] = accepted
        _write_json(current / "state.json", state)
    diagnostics = _read_diagnostics(current)
    invocations = diagnostics.setdefault("role_invocations", {})
    if isinstance(invocations, dict):
        invocations[role] = int(invocations.get(role, 0) or 0) + 1
    used = diagnostics.setdefault("protocol_correction_used", {})
    if isinstance(used, dict):
        used[role] = False
    _write_diagnostics(current, diagnostics)
    (current / f"contract-correction-{role}.md").unlink(missing_ok=True)

def _mark_role_accepted(current: Path, role: str, outputs: list[Path]) -> None:
    state_value = _read_json(current / "state.json")
    if not isinstance(state_value, dict) or not state_value:
        return
    state = state_value
    contract = role_contracts().get(role, {})
    relative = str(contract.get("output_artifact", ""))
    path = current / Path(relative).name if relative.startswith(".autodev-run/current/") else None
    digest = _file_sha256(path) if path is not None else ""
    accepted = state.setdefault("AcceptedRoleArtifacts", {})
    if isinstance(accepted, dict):
        accepted[role] = {
            "artifact": relative,
            "sha256": digest,
        }
    state["OpenCodeProtocolVersion"] = OPENCODE_PROTOCOL_VERSION
    _write_json(current / "state.json", state)

def _reset_current_correction(current: Path, role: str) -> None:
    diagnostics = _read_diagnostics(current)
    used = diagnostics.setdefault("protocol_correction_used", {})
    if isinstance(used, dict):
        used[role] = False
    _write_diagnostics(current, diagnostics)

def _contract_output_path(current: Path, role: str) -> Path | None:
    relative = str(role_contracts().get(role, {}).get("output_artifact", ""))
    if relative.startswith(".autodev-run/current/"):
        return current / Path(relative).name
    return None

def _resolved_policies(repo: Path, state: dict[str, object]) -> dict[str, str]:
    profile_value = str(state.get("ProviderProfile", "")).strip()
    if not profile_value:
        return resolve_prompt_policies({})
    profile = Path(profile_value).expanduser()
    if not profile.is_absolute():
        profile = repo / profile
    try:
        config = load_provider_config(str(profile))
    except (OSError, json.JSONDecodeError):
        config = {}
    return resolve_prompt_policies(config)

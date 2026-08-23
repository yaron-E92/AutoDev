from __future__ import annotations

import hashlib
from pathlib import Path

from automation.windows_verification_config import (
    load_config,
    parse_deferred_obligations,
    safe_config_metadata,
)
from automation.windows_verification_manifest import (
    sync_manifest,
)
from automation.windows_verification_storage import (
    _write_json,
)

def record_local_deferred_obligations(
    repo: Path,
    current: Path,
    state: dict[str, object],
    output: str,
) -> dict[str, object]:
    obligations = parse_deferred_obligations(output)
    config = load_config(repo)
    windows_from_log = any(item.get("platform") == "windows" for item in obligations)
    always = bool(config and config.get("enabled") and config.get("when") == "always")
    required = windows_from_log or always
    if always and not windows_from_log:
        message = "Repository Windows verification policy requires this shipped patch to run on Windows."
        obligations.append(
            {
                "id": hashlib.sha256(f"windows|{message}".encode("utf-8")).hexdigest()[:16],
                "platform": "windows",
                "message": message,
                "source": "repository-policy",
            }
        )

    state["DeferredVerificationObligations"] = obligations
    state["WindowsVerificationRequired"] = required
    state["WindowsVerificationConfig"] = safe_config_metadata(config)
    state.pop("WindowsVerificationProof", None)
    state.pop("LastWindowsVerificationFailure", None)
    _write_json(current / "state.json", state)
    _write_json(
        current / "deferred-verification.json",
        {
            "obligations": obligations,
            "windows_required": required,
            "windows_config": safe_config_metadata(config),
        },
    )
    sync_manifest(repo, state)
    return {
        "deferred_verification_obligations": obligations,
        "windows_verification_required": required,
        "windows_verification_config": safe_config_metadata(config),
    }

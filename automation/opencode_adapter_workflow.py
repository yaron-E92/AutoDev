from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from automation import opencode_resume
from automation import workflow_stages

from automation.opencode_adapter_contract import (
    AUTODEV_ROOT,
    CURRENT_DIR,
    OpenCodeAdapterError,
)
from automation.opencode_adapter_models import (
    reject_unsupported_model_overrides,
    resolve_opencode_model_mappings,
)
from automation.opencode_adapter_protocol import (
    _begin_role_invocation,
    _ensure_opencode_protocol,
)
from automation.opencode_adapter_storage import (
    _read_state,
)

def workflow_stage(
    name: str,
    repo: Path,
    *,
    arguments: str = "",
    autodev_root: Path = AUTODEV_ROOT,
    attempt: int = 0,
    reason: str = "",
    runner=subprocess.run,
    which=shutil.which,
) -> tuple[int, dict[str, object]]:
    reject_unsupported_model_overrides(arguments)
    repo = repo.expanduser().resolve()
    try:
        code, payload = workflow_stages.execute_stage(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )
        if name == "preflight" and payload.get("state") == "CONTINUE":
            resolve_opencode_model_mappings(repo, runner=runner)
    except workflow_stages.WorkflowStageError as exc:
        opencode_resume.checkpoint_failure(repo, name, exc)
        raise OpenCodeAdapterError(
            str(exc),
            classification=exc.classification,
        ) from exc

    current = repo / CURRENT_DIR
    if name == "prepare" and payload.get("state") == "CONTINUE" and current.is_dir():
        _ensure_opencode_protocol(current)
        opencode_resume.create_open_code_manifest(repo, _read_state(current))
    elif name == "render-implementer" and payload.get("state") == "CONTINUE" and current.is_dir():
        _ensure_opencode_protocol(current)
        _begin_role_invocation(current, "implementer")
    if name != "prepare" and opencode_resume.has_manifest(repo):
        opencode_resume.checkpoint_stage(repo, name, payload, attempt)
    return code, payload

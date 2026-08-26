from __future__ import annotations

import os
import shutil
from pathlib import Path

from automation import workflow_prompts
from automation.workflow_contract import WorkflowStageError


def install() -> None:
    from automation import repo_setup

    current = repo_setup.doctor
    if getattr(current, "_autodev_local_verification", False):
        return
    original = current

    def doctor(repo: Path, **kwargs):
        result = original(repo, **kwargs)
        root = Path(
            kwargs.get("autodev_root")
            or Path(repo_setup.__file__).resolve().parents[1]
        ).expanduser().resolve()
        which = kwargs.get("which", shutil.which)
        profiles_path = Path(
            os.environ.get("PROFILES_PATH", str(root / "codex-profiles.json"))
        ).expanduser()
        try:
            profiles_csv, command, _ = workflow_prompts.resolve_profiles(
                [],
                profiles_path,
                explicit_profiles=os.environ.get("PROFILES", ""),
                explicit_local_check=os.environ.get("LOCAL_CHECK", ""),
                explicit_stack_context=os.environ.get("STACK_CONTEXT", ""),
                autodev_root=root,
                which=which,
            )
            check = repo_setup.DoctorCheck(
                "local-verification",
                "ok",
                f"profiles={profiles_csv}; command={command}",
            )
        except WorkflowStageError as exc:
            check = repo_setup.DoctorCheck(
                "local-verification",
                "error",
                str(exc),
            )
        return repo_setup.DoctorResult(
            result.repository,
            (*result.checks, check),
            fixed=result.fixed,
        )

    doctor._autodev_local_verification = True  # type: ignore[attr-defined]
    repo_setup.doctor = doctor

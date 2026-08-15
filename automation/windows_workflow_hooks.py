from __future__ import annotations

from pathlib import Path

from automation import windows_verification


def install(core) -> None:
    """Layer Windows verification onto the already-composed workflow stage API."""

    if getattr(core, "_autodev_windows_workflow_hooks_installed", False):
        return
    windows_verification.install_manifest_hooks()
    original_execute_stage = core.execute_stage

    def execute_stage(
        name: str,
        repo: Path,
        *,
        arguments: str = "",
        autodev_root: Path = core.AUTODEV_ROOT,
        attempt: int = 0,
        reason: str = "",
        runner=core.subprocess.run,
        which=core.shutil.which,
    ) -> tuple[int, dict[str, object]]:
        repo = repo.expanduser().resolve()
        current = repo / core.CURRENT_DIR

        if name == "preflight":
            try:
                windows_verification.validate_config(repo)
            except windows_verification.WindowsVerificationError as exc:
                raise core.WorkflowStageError(str(exc)) from exc

        if name == "ready" and current.is_dir():
            try:
                windows_verification.validate_ready(current, core.read_state(current))
            except windows_verification.WindowsVerificationError as exc:
                raise core.WorkflowStageError(str(exc)) from exc

        code, payload = original_execute_stage(
            name,
            repo,
            arguments=arguments,
            autodev_root=autodev_root,
            attempt=attempt,
            reason=reason,
            runner=runner,
            which=which,
        )

        if name == "local-check" and payload.get("state") == "CONTINUE":
            state = core.read_state(current)
            output = core.read_text(current / "local-check.log")
            try:
                payload.update(
                    windows_verification.record_local_deferred_obligations(
                        repo,
                        current,
                        state,
                        output,
                    )
                )
            except windows_verification.WindowsVerificationError as exc:
                raise core.WorkflowStageError(str(exc)) from exc

        if name == "pr-and-ci" and payload.get("state") == "CONTINUE":
            state = core.read_state(current)
            max_attempts = core.configured_attempt_limit(
                "MAX_REPAIR_ATTEMPTS",
                core.DEFAULT_MAX_REPAIR_ATTEMPTS,
            )
            try:
                windows = windows_verification.run_after_ci(
                    repo,
                    current,
                    state,
                    max_repair_attempts=max_attempts,
                    runner=runner,
                )
            except windows_verification.WindowsVerificationError as exc:
                raise core.WorkflowStageError(str(exc)) from exc
            if windows is not None:
                payload.update(windows)
            else:
                payload.update(windows_verification.payload_metadata(state))

        if name in {"ready", "status"} and current.is_dir():
            payload.update(windows_verification.payload_metadata(core.read_state(current)))

        return code, payload

    core.execute_stage = execute_stage
    core._autodev_windows_workflow_hooks_installed = True

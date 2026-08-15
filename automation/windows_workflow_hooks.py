from __future__ import annotations

import os
from pathlib import Path

from automation import windows_verification


def install(core) -> None:
    """Layer GitHub-hosted Windows verification onto the shared workflow API."""

    if getattr(core, "_autodev_windows_workflow_hooks_installed", False):
        return
    windows_verification.install_manifest_hooks()
    original_execute_stage = core.execute_stage

    def _windows_pr_and_ci(
        repo: Path,
        current: Path,
        state: dict[str, object],
        autodev_root: Path,
        *,
        attempt: int,
        runner,
    ) -> dict[str, object]:
        max_attempts = core.configured_attempt_limit(
            "MAX_REPAIR_ATTEMPTS",
            core.DEFAULT_MAX_REPAIR_ATTEMPTS,
        )

        changes = core.workspace_changes(repo, current, state)
        if changes:
            core.write_json(current / "changed-files.json", changes)
            commit_sha = core.create_api_commit(repo, state, changes, current, runner=runner)
            state = core.read_state(current)
            state["LastCommitSha"] = commit_sha
            state["Status"] = "CommittedViaGitHubApi"
            state.pop("CommitTreeBaseSha", None)
            core.write_state(current, state)
            snapshot_path = current / "last-commit-workspace-snapshot.json"
            core.write_workspace_snapshot(repo, snapshot_path)
            state = core.read_state(current)
            state["LastCommitSnapshotHash"] = core._file_sha256(snapshot_path)
            core.write_state(current, state)
        elif not str(state.get("LastCommitSha", "")).strip() and not str(state.get("PrUrl", "")).strip():
            raise core.WorkflowStageError("no workspace file changes detected, and no pushed AutoDev commit exists")

        state = core.read_state(current)
        windows = windows_verification.run_after_push(
            repo,
            current,
            state,
            max_repair_attempts=max_attempts,
            runner=runner,
        )
        if windows is not None and windows.get("state") != "CONTINUE":
            return windows

        # The Windows lane is deliberately before PR creation. workflow_dispatch
        # targets the just-pushed AutoDev branch, so the GitHub-hosted Windows job
        # can validate the exact commit without waiting for a PR to exist.
        state = core.read_state(current)
        core.ensure_pr(repo, current, state, runner=runner)
        state = core.read_state(current)
        ci_proof = core.wait_for_required_checks(repo, state, runner=runner)
        core.write_json(current / "ci-summary.json", ci_proof)
        state = core.read_state(current)
        state["CiProof"] = ci_proof
        if ci_proof["state"] == "terminal-failure":
            core.render_ci_repair(current, state, autodev_root)
            state["Status"] = "CiFailed"
            core.write_state(current, state)
            if attempt >= max_attempts:
                result = core.stage_payload(
                    repo,
                    "BLOCKED",
                    "pr-and-ci",
                    reason="CI repair-attempt limit exhausted",
                    artifact=current / "ci-repair.md",
                    failure_classification=core.FAILURE_DETERMINISTIC,
                    next_action="mark the run blocked",
                    max_repair_attempts=max_attempts,
                )
            else:
                result = core.stage_payload(
                    repo,
                    "REPAIR",
                    "pr-and-ci",
                    reason="required PR checks failed",
                    artifact=current / "ci-repair.md",
                    failure_classification=core.FAILURE_CODE_REPAIRABLE,
                    next_action="delegate the CI repair to autodev-fixer, increment the attempt, rerun local-check and semantic verification, then retry shipment",
                    max_repair_attempts=max_attempts,
                )
            if windows is not None:
                result.update(
                    {
                        key: value
                        for key, value in windows.items()
                        if key
                        in {
                            "platform_verification_stage",
                            "windows_repair_attempt",
                            "windows_verification_proof",
                            "windows_stage_completed",
                        }
                    }
                )
            return result
        if ci_proof["state"] != "terminal-success":
            raise core.WorkflowStageError(
                f"required CI did not reach terminal success for {ci_proof.get('head_sha', '')}: {ci_proof.get('state', '')}",
                classification=core.FAILURE_TRANSIENT,
            )

        core.render_legacy_verifier(repo, current, state, autodev_root, runner=runner)
        state["Status"] = "CiPassedVerifierPromptRendered"
        core.write_state(current, state)
        result = core.stage_payload(
            repo,
            "CONTINUE",
            "pr-and-ci",
            next_action="mark the PR ready for human review",
            max_repair_attempts=max_attempts,
        )
        if windows is not None:
            result.update(windows)
            result["state"] = "CONTINUE"
            result["failed_stage"] = ""
            result["failure_classification"] = ""
            result["next_action"] = "mark the PR ready for human review"
        return result

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
                config = windows_verification.load_config(repo)
                if config and bool(config.get("enabled", True)):
                    owner = os.environ.get("GITHUB_OWNER", "").strip()
                    repo_name = os.environ.get("GITHUB_REPO", "").strip()
                    if owner and repo_name:
                        windows_verification.validate_actions_installation(
                            repo,
                            repo_full=f"{owner}/{repo_name}",
                            config=config,
                            runner=runner,
                        )
            except windows_verification.WindowsVerificationError as exc:
                raise core.WorkflowStageError(str(exc)) from exc

        if name == "ready" and current.is_dir():
            try:
                windows_verification.validate_ready(current, core.read_state(current))
            except windows_verification.WindowsVerificationError as exc:
                raise core.WorkflowStageError(str(exc)) from exc

        if name == "pr-and-ci" and current.is_dir():
            state = core.read_state(current)
            if windows_verification.windows_required(state):
                if state.get("OpenCodeProtocolVersion"):
                    if not bool(state.get("LastLocalCheckPassed")):
                        raise core.WorkflowStageError(
                            "pr-and-ci prerequisite not met: deterministic local verification has not passed"
                        )
                    if str(state.get("LastSemanticVerdict", "")) != "pass":
                        raise core.WorkflowStageError(
                            "pr-and-ci prerequisite not met: semantic verification has not produced an accepted pass verdict"
                        )
                try:
                    payload = _windows_pr_and_ci(
                        repo,
                        current,
                        state,
                        Path(autodev_root).expanduser().resolve(),
                        attempt=attempt,
                        runner=runner,
                    )
                except windows_verification.WindowsVerificationError as exc:
                    raise core.WorkflowStageError(str(exc)) from exc
                return 0, payload

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

        if name in {"ready", "status"} and current.is_dir():
            payload.update(windows_verification.payload_metadata(core.read_state(current)))

        return code, payload

    core.execute_stage = execute_stage
    core._autodev_windows_workflow_hooks_installed = True

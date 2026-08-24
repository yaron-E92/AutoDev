from __future__ import annotations

from automation import opencode_adapter_models

from automation import opencode_adapter_contract

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from automation import (
    opencode_cli,
    privacy,
    role_runtime,
    run_manifest,
)


class OpenCodeRoleRuntime:
    name = "opencode"

    def __init__(self) -> None:
        self._mappings: dict[str, dict[str, str]] = {}

    def validate_arguments(self, arguments: str) -> None:
        opencode_adapter_models.reject_unsupported_model_overrides(arguments)

    def _resolve_mappings(
        self,
        repo: Path,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> dict[str, dict[str, str]]:
        if not self._mappings:
            self._mappings = opencode_adapter_models.resolve_opencode_model_mappings(
                repo,
                runner=runner,
                which=which,
            )
        return self._mappings

    def role_snapshots(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> dict[str, object]:
        mappings = self._resolve_mappings(repo, runner=runner, which=which)
        snapshots: dict[str, object] = {}
        for role in opencode_adapter_contract.ROLE_NAMES:
            mapping = mappings.get(role, {})
            model = str(mapping.get("model", ""))
            provider = model.split("/", 1)[0] if "/" in model else ""
            agent = str(mapping.get("agent", f"autodev-{role}"))

            # Keep the pre-#160 OpenCode fingerprint shape exactly. Runtime
            # identity already participated as transport=opencode, so adding a
            # second runtime field would falsely invalidate in-progress runs.
            configured = {
                "transport": self.name,
                "agent": agent,
                "model": model,
                "source": str(mapping.get("source", "inherited")),
                "inherits_from": str(mapping.get("inherits_from", "")),
            }
            safe = {
                "transport": self.name,
                "provider": provider,
                "profile_name": str(mapping.get("source", "inherited")),
                "model": model,
                "agent": agent,
            }
            snapshots[role] = run_manifest.build_role_snapshot(configured, safe)
        return snapshots

    def invoke(
        self,
        context: role_runtime.RoleInvocationContext,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> role_runtime.RoleInvocationResult:
        repo = context.repo.expanduser().resolve()
        try:
            executable = opencode_cli.resolve_opencode_cli(which=which)
        except opencode_cli.OpenCodeCliError as exc:
            raise role_runtime.RoleRuntimeError(str(exc)) from exc

        environment = dict(os.environ)
        model = ""
        try:
            mappings = self._resolve_mappings(repo, runner=runner, which=which)
            model = str(mappings.get(context.role, {}).get("model", "")).strip()
            policy = privacy.load_policy(repo)
            if policy.enabled:
                # The legacy coordinator installed this immediately before every
                # OpenCode subprocess. Keep that boundary here so batch consent,
                # exact environment consent, and persistent timed grants are all
                # resolved before any role prompt can leave the machine.
                from automation import privacy_consent

                privacy_consent.ensure_run_consent(
                    repo,
                    mappings,
                    executable=executable,
                    runner=runner,
                )
                if not model:
                    raise privacy.PrivacyError(
                        f"cannot resolve the effective OpenCode model for AutoDev role {context.role}; privacy cannot be verified"
                    )
                decision, environment = privacy.authorize_opencode_role(
                    repo,
                    role=context.role,
                    model=model,
                    opencode_cli=executable,
                    runner=runner,
                    base_env=environment,
                )
                print(
                    json.dumps(
                        {"event": "privacy", **decision.safe_metadata()},
                        sort_keys=True,
                    ),
                    flush=True,
                )
        except privacy.PrivacyError as exc:
            raise role_runtime.RoleRuntimeError(
                str(exc),
                classification=exc.classification,
            ) from exc
        except opencode_adapter_contract.OpenCodeAdapterError as exc:
            raise role_runtime.RoleRuntimeError(
                str(exc),
                classification=exc.classification,
            ) from exc

        command = [
            executable,
            "run",
            "--agent",
            f"autodev-{context.role}",
            "--dir",
            str(repo),
            "--format",
            "json",
            context.prompt,
        ]
        started = time.monotonic()
        try:
            completed = runner(
                command,
                cwd=repo,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=context.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return role_runtime.RoleInvocationResult(
                runtime=self.name,
                role=context.role,
                phase=context.phase,
                returncode=None,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                stdout=_text(
                    getattr(exc, "stdout", "")
                    or getattr(exc, "output", "")
                ),
                stderr=_text(getattr(exc, "stderr", "")),
                termination="runtime-timeout",
                model=model,
            )
        except OSError as exc:
            return role_runtime.RoleInvocationResult(
                runtime=self.name,
                role=context.role,
                phase=context.phase,
                returncode=None,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                stderr=str(exc),
                termination="runtime-launch-failed",
                model=model,
            )

        returncode = int(getattr(completed, "returncode", 1))
        return role_runtime.RoleInvocationResult(
            runtime=self.name,
            role=context.role,
            phase=context.phase,
            returncode=returncode,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            stdout=_text(getattr(completed, "stdout", "")),
            stderr=_text(getattr(completed, "stderr", "")),
            termination="completed" if returncode == 0 else "runtime-nonzero",
            model=model,
        )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")

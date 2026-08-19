from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from automation import opencode_adapter, opencode_cli, privacy, role_runtime, workflow_stages


class OpenCodeRoleRuntime:
    name = "opencode"

    def role_snapshots(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> dict[str, object]:
        mappings = opencode_adapter.resolve_opencode_model_mappings(
            repo,
            runner=runner,
            which=which,
        )
        snapshots: dict[str, object] = {}
        for role in opencode_adapter.ROLE_NAMES:
            mapping = mappings.get(role, {})
            model = str(mapping.get("model", ""))
            provider = model.split("/", 1)[0] if "/" in model else ""
            agent = str(mapping.get("agent", f"autodev-{role}"))
            snapshots[role] = role_runtime.build_role_snapshot(
                runtime=self.name,
                role=role,
                configured={
                    "agent": agent,
                    "model": model,
                    "source": str(mapping.get("source", "inherited")),
                    "inherits_from": str(mapping.get("inherits_from", "")),
                },
                safe_metadata={
                    "transport": self.name,
                    "provider": provider,
                    "profile_name": str(mapping.get("source", "inherited")),
                    "model": model,
                    "agent": agent,
                },
            )
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
            mappings = opencode_adapter.resolve_opencode_model_mappings(
                repo,
                runner=runner,
                which=which,
            )
            model = str(mappings.get(context.role, {}).get("model", "")).strip()
            policy = privacy.load_policy(repo)
            if policy.enabled:
                if not model:
                    raise privacy.PrivacyError(
                        f"cannot resolve the effective OpenCode model for AutoDev role {context.role}; privacy cannot be verified"
                    )
                _, environment = privacy.authorize_opencode_role(
                    repo,
                    role=context.role,
                    model=model,
                    opencode_cli=executable,
                    runner=runner,
                    base_env=environment,
                )
        except privacy.PrivacyError as exc:
            raise role_runtime.RoleRuntimeError(
                str(exc),
                classification=exc.classification,
            ) from exc
        except opencode_adapter.OpenCodeAdapterError as exc:
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
                stdout=_text(getattr(exc, "stdout", "") or getattr(exc, "output", "")),
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

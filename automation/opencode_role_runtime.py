from __future__ import annotations

from automation import opencode_adapter_assets, opencode_adapter_models
from automation import opencode_adapter_contract

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from automation import (
    opencode_cli,
    opencode_cli_text,
    opencode_privacy_adapter,
    opencode_schema_fallback,
    opencode_structured_output,
    privacy,
    privacy_authorization,
    role_output_contract,
    role_output_fallback,
    role_runtime,
    run_manifest,
)


SCHEMA_FALLBACK_STATE = opencode_schema_fallback.SCHEMA_FALLBACK_STATE
# Backward-compatible public/test alias retained for the original #288 Reader hotfix.
READER_SCHEMA_FALLBACK_STATE = SCHEMA_FALLBACK_STATE
FALLBACK_CORRECTION_STATE = "protocol-correction-after-fallback"


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
            contract = role_output_contract.contract_for_role(role)

            # Keep the pre-#160 OpenCode fingerprint shape for roles that have no
            # structured contract. A versioned output contract intentionally joins
            # the fingerprint once that role migrates because its durable output
            # meaning/resume compatibility has changed.
            configured: dict[str, object] = {
                "transport": self.name,
                "agent": agent,
                "model": model,
                "source": str(mapping.get("source", "inherited")),
                "inherits_from": str(mapping.get("inherits_from", "")),
            }
            safe: dict[str, object] = {
                "transport": self.name,
                "provider": provider,
                "profile_name": str(mapping.get("source", "inherited")),
                "model": model,
                "agent": agent,
            }
            if contract is not None:
                metadata = contract.safe_metadata()
                configured["output_contract"] = metadata
                safe["output_contract"] = metadata
            snapshots[role] = run_manifest.build_role_snapshot(configured, safe)
        return snapshots

    def structured_output_capability(
        self,
        context: role_runtime.RoleInvocationContext,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> str:
        contract = context.output_contract
        if contract is None:
            return role_output_contract.CAPABILITY_UNSUPPORTED
        if opencode_cli_text.correction_after_fallback(
            context.repo,
            role=context.role,
            phase=context.phase,
            has_contract=True,
        ):
            return role_output_contract.CAPABILITY_EMULATED
        mappings = self._resolve_mappings(context.repo, runner=runner, which=which)
        model = str(mappings.get(context.role, {}).get("model", "")).strip()
        if not model or "/" not in model:
            return role_output_contract.CAPABILITY_UNSUPPORTED
        # OpenCode's session API owns schema validation/retries. Whether an older
        # installed OpenCode actually exposes that API is probed during invocation;
        # unsupported versions fall back to the existing CLI/text protocol.
        contract.schema()
        return role_output_contract.CAPABILITY_NATIVE_VALIDATED

    def provision_scheduler_worker(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> None:
        repo = repo.expanduser().resolve()
        try:
            opencode_adapter_assets.provision_scheduler_worker_assets(
                repo,
                runner=runner,
            )
        except opencode_adapter_contract.OpenCodeAdapterError as exc:
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime provisioning failed for {self.name}: {exc}"
            ) from exc
        # Provisioning may add/refresh agent definitions that affect resolved
        # OpenCode configuration, so stale mappings must never survive it.
        self._mappings = {}

    def validate_scheduler_worker(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> None:
        repo = repo.expanduser().resolve()
        try:
            executable = opencode_cli.resolve_opencode_cli(which=which)
        except opencode_cli.OpenCodeCliError as exc:
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime preflight failed for {self.name}: {exc}"
            ) from exc
        try:
            discovery_env = dict(os.environ)
            discovery_env["NO_COLOR"] = "1"
            completed = runner(
                [executable, "agent", "list"],
                cwd=repo,
                env=discovery_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime preflight failed for {self.name}: "
                f"OpenCode agent discovery could not be launched: {exc}"
            ) from exc
        returncode = int(getattr(completed, "returncode", 1))
        if returncode != 0:
            stderr = str(getattr(completed, "stderr", "") or "").strip()
            detail = f": {stderr}" if stderr else ""
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime preflight failed for {self.name}: "
                f"`opencode agent list` exited with code {returncode}{detail}"
            )
        output = str(getattr(completed, "stdout", "") or "")
        required = [
            opencode_adapter_contract.AUTODEV_AGENT_BY_ROLE[role]
            for role in opencode_adapter_contract.ROLE_NAMES
        ]
        missing = [
            agent
            for agent in required
            if re.search(
                rf"(?m)^\s*{re.escape(agent)}\s+\(",
                output,
            )
            is None
        ]
        if missing:
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime preflight failed for {self.name}: required "
                "AutoDev agent(s) are not discoverable in the dedicated worker: "
                + ", ".join(missing)
                + ". AutoDev provisioned the maintained runtime assets, but "
                "OpenCode still cannot resolve them; inspect the worker/runtime "
                "installation and retry `autodev scheduler install`."
            )

        # Reuse the same resolved configuration for model/privacy preflight.
        try:
            config = opencode_adapter_models.resolve_opencode_config(
                repo,
                runner=runner,
                which=which,
            )
            self._mappings = opencode_adapter_models.apply_autodev_model_profile(
                repo,
                opencode_adapter_models.model_mappings_from_config(config),
            )
        except opencode_adapter_contract.OpenCodeAdapterError as exc:
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime preflight failed for {self.name}: {exc}"
            ) from exc

    def privacy_evidence(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> dict[str, privacy.PrivacyDecision]:
        repo = repo.expanduser().resolve()
        try:
            executable = opencode_cli.resolve_opencode_cli(which=which)
        except opencode_cli.OpenCodeCliError as exc:
            raise role_runtime.RoleRuntimeError(str(exc)) from exc
        mappings = self._resolve_mappings(repo, runner=runner, which=which)
        evidence: dict[str, privacy.PrivacyDecision] = {}
        for role in opencode_adapter_contract.ROLE_NAMES:
            model = str(mappings.get(role, {}).get("model", "")).strip()
            if not model:
                raise role_runtime.RoleRuntimeError(
                    f"cannot resolve the effective {self.name} model for AutoDev role {role}; "
                    "privacy cannot be verified"
                )
            decision, _ = opencode_privacy_adapter.evaluate_role(
                repo,
                role=role,
                model=model,
                opencode_cli=executable,
                runner=runner,
            )
            evidence[role] = decision
        return evidence

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
                evidence, environment = opencode_privacy_adapter.evaluate_role(
                    repo,
                    role=context.role,
                    model=model,
                    opencode_cli=executable,
                    runner=runner,
                    base_env=environment,
                )
                decision = privacy_authorization.authorize_evaluated(
                    repo,
                    evidence,
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

        contract = context.output_contract
        fallback_only = opencode_cli_text.correction_after_fallback(
            repo,
            role=context.role,
            phase=context.phase,
            has_contract=contract is not None,
        )
        fallback_state = FALLBACK_CORRECTION_STATE if fallback_only else ""
        if contract is not None and not fallback_only:
            capability = self.structured_output_capability(
                context,
                runner=runner,
                which=which,
            )
            if capability in {
                role_output_contract.CAPABILITY_NATIVE_STRICT,
                role_output_contract.CAPABILITY_NATIVE_VALIDATED,
            }:
                started = time.monotonic()
                try:
                    native = opencode_structured_output.invoke(
                        executable=executable,
                        repo=repo,
                        role=context.role,
                        model=model,
                        prompt=context.prompt,
                        contract=contract,
                        environment=environment,
                        timeout_seconds=context.timeout_seconds,
                    )
                    role_output_contract.materialize_structured_output(
                        repo,
                        contract,
                        native.value,
                    )
                except opencode_structured_output.StructuredOutputUnavailable:
                    # Backward compatibility: an older installed OpenCode may still
                    # be a perfectly valid CLI/text runtime. Native capability is
                    # opportunistic and never mandatory for an otherwise supported
                    # runtime.
                    fallback_state = "native-unavailable"
                except opencode_structured_output.StructuredOutputExhausted as exc:
                    if role_output_fallback.supported(contract):
                        return self._schema_exhaustion_fallback(
                            context,
                            executable=executable,
                            repo=repo,
                            model=model,
                            environment=environment,
                            runner=runner,
                            native_started=started,
                            native_error=exc,
                        )
                    return self._structured_result(
                        context,
                        model=model,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        termination="structured-output-exhausted",
                        state="schema-exhausted",
                        retry_count=exc.retries,
                        stderr=str(exc),
                    )
                except (
                    opencode_structured_output.StructuredOutputTransportError,
                    role_output_contract.RoleOutputContractError,
                ) as exc:
                    return self._structured_result(
                        context,
                        model=model,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        termination="structured-output-runtime-failed",
                        state="runtime-failed",
                        stderr=str(exc),
                    )
                else:
                    return self._structured_result(
                        context,
                        model=model,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        termination="completed",
                        state="schema-validated",
                        retry_count=native.retries,
                        returncode=0,
                    )

        return self._invoke_cli(
            context,
            executable=executable,
            repo=repo,
            model=model,
            environment=environment,
            runner=runner,
            fallback_state=fallback_state,
        )

    def _schema_exhaustion_fallback(
        self,
        context: role_runtime.RoleInvocationContext,
        *,
        executable: str,
        repo: Path,
        model: str,
        environment: dict[str, str],
        runner: Callable[..., object],
        native_started: float,
        native_error: opencode_structured_output.StructuredOutputExhausted,
    ) -> role_runtime.RoleInvocationResult:
        contract = context.output_contract
        if contract is None or not role_output_fallback.supported(contract):
            return self._structured_result(
                context,
                model=model,
                elapsed_ms=int((time.monotonic() - native_started) * 1000),
                termination="structured-output-exhausted",
                state="schema-exhausted",
                retry_count=native_error.retries,
                stderr=str(native_error),
            )

        # The native retry budget has already been consumed. Call the CLI/text seam
        # directly so this logical invocation can never start a second native schema
        # sequence. Remove only the contract's prior durable artifact so stale output
        # cannot masquerade as this fallback attempt; a compatibility self-written
        # artifact created by the fallback invocation itself remains eligible for #307.
        target = repo / ".autodev-run" / "current" / contract.output_artifact
        target.unlink(missing_ok=True)
        fallback = self._invoke_cli(
            context,
            executable=executable,
            repo=repo,
            model=model,
            environment=environment,
            runner=runner,
            fallback_state=SCHEMA_FALLBACK_STATE,
            schema_retry_count=native_error.retries,
            prompt_override=opencode_schema_fallback.prompt(context.prompt, contract),
        )
        total_elapsed = int((time.monotonic() - native_started) * 1000)

        # Preserve the #288 Reader compatibility diagnostics and deterministic
        # terminal behavior while the generic #307 materialization evidence remains
        # authoritative for all supported roles.
        if contract.role == "reader" and fallback.termination == "completed" and fallback.returncode == 0:
            try:
                opencode_cli_text.materialize_reader_fallback(
                    repo,
                    contract,
                    fallback.stdout,
                )
                opencode_cli_text.record_reader_fallback_materialization(
                    repo,
                    opencode_cli_text.FALLBACK_MATERIALIZATION_CAPTURED_TEXT,
                )
            except opencode_adapter_contract.OpenCodeAdapterError as exc:
                detail = str(exc)
                try:
                    opencode_cli_text.record_reader_fallback_materialization(
                        repo,
                        opencode_cli_text.FALLBACK_MATERIALIZATION_REJECTED,
                    )
                except opencode_adapter_contract.OpenCodeAdapterError as diagnostics_exc:
                    detail += f"; diagnostic persistence failed: {diagnostics_exc}"
                return opencode_schema_fallback.failure_result(
                    fallback,
                    elapsed_ms=total_elapsed,
                    retries=native_error.retries,
                    detail=f"fallback-text Reader output rejected: {detail}",
                )

        if fallback.termination != "completed" or fallback.returncode != 0:
            if contract.role == "reader":
                try:
                    opencode_cli_text.record_reader_fallback_materialization(
                        repo,
                        opencode_cli_text.FALLBACK_MATERIALIZATION_INVOCATION_FAILED,
                    )
                except opencode_adapter_contract.OpenCodeAdapterError:
                    pass
            detail = (
                fallback.stderr
                or fallback.stdout
                or f"fallback-text {contract.role} invocation failed"
            )
            return opencode_schema_fallback.failure_result(
                fallback,
                elapsed_ms=total_elapsed,
                retries=native_error.retries,
                detail=detail,
            )

        return role_runtime.RoleInvocationResult(
            runtime=fallback.runtime,
            role=fallback.role,
            phase=fallback.phase,
            returncode=fallback.returncode,
            elapsed_ms=total_elapsed,
            stdout=fallback.stdout,
            stderr=fallback.stderr,
            termination="completed",
            model=fallback.model,
            structured_output_mode="fallback-text",
            structured_output_state=SCHEMA_FALLBACK_STATE,
            contract_name=fallback.contract_name,
            contract_version=fallback.contract_version,
            schema_retry_count=max(0, int(native_error.retries)),
        )

    def _structured_result(
        self,
        context: role_runtime.RoleInvocationContext,
        *,
        model: str,
        elapsed_ms: int,
        termination: str,
        state: str,
        retry_count: int = 0,
        returncode: int | None = None,
        stderr: str = "",
    ) -> role_runtime.RoleInvocationResult:
        contract = context.output_contract
        return role_runtime.RoleInvocationResult(
            runtime=self.name,
            role=context.role,
            phase=context.phase,
            returncode=returncode,
            elapsed_ms=elapsed_ms,
            stderr=stderr,
            termination=termination,
            model=model,
            structured_output_mode=role_output_contract.CAPABILITY_NATIVE_VALIDATED,
            structured_output_state=state,
            contract_name=contract.name if contract is not None else "",
            contract_version=contract.version if contract is not None else 0,
            schema_retry_count=max(0, int(retry_count)),
        )

    def _invoke_cli(
        self,
        context: role_runtime.RoleInvocationContext,
        *,
        executable: str,
        repo: Path,
        model: str,
        environment: dict[str, str],
        runner: Callable[..., object],
        fallback_state: str,
        schema_retry_count: int = 0,
        prompt_override: str = "",
    ) -> role_runtime.RoleInvocationResult:
        command = [
            executable,
            "run",
            "--agent",
            f"autodev-{context.role}",
        ]
        if model:
            command.extend(["--model", model])
        command.extend([
            "--dir",
            str(repo),
            "--format",
            "json",
            prompt_override or context.prompt,
        ])
        started = time.monotonic()
        contract = context.output_contract
        metadata = {
            "structured_output_mode": "fallback-text",
            "structured_output_state": fallback_state or ("fallback" if contract else ""),
            "contract_name": contract.name if contract is not None else "",
            "contract_version": contract.version if contract is not None else 0,
            "schema_retry_count": max(0, int(schema_retry_count)),
        }
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
                **metadata,
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
                **metadata,
            )

        returncode = int(getattr(completed, "returncode", 1))
        stdout = _text(getattr(completed, "stdout", ""))
        stderr = _text(getattr(completed, "stderr", ""))
        if returncode == 0 and contract is not None:
            outcome = opencode_cli_text.materialize_fallback_result(
                repo,
                contract,
                stdout,
            )
            try:
                opencode_cli_text.record_fallback_materialization(
                    repo,
                    role=context.role,
                    phase=context.phase,
                    outcome=outcome,
                )
            except opencode_adapter_contract.OpenCodeAdapterError as exc:
                diagnostic_error = f"fallback materialization diagnostics failed: {exc}"
                stderr = (stderr.rstrip() + "\n" + diagnostic_error).strip()

        return role_runtime.RoleInvocationResult(
            runtime=self.name,
            role=context.role,
            phase=context.phase,
            returncode=returncode,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            stdout=stdout,
            stderr=stderr,
            termination="completed" if returncode == 0 else "runtime-nonzero",
            model=model,
            **metadata,
        )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")

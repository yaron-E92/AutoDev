from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from automation import (
    opencode_adapter_contract,
    opencode_adapter_models,
    opencode_cli,
    opencode_multimodal,
    opencode_privacy_adapter,
    privacy,
    privacy_authorization,
    ux_multimodal,
)


class UXMultimodalRuntimeError(RuntimeError):
    pass


class RuntimeAdapter:
    def __init__(self, runtime: object) -> None:
        self.runtime = runtime
        self.name = str(getattr(runtime, "name", "") or "")

    def multimodal_verifier_capability(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> str:
        if self.name != "opencode":
            return ux_multimodal.CAPABILITY_UNSUPPORTED
        try:
            mappings = self._opencode_mappings(repo, runner=runner, which=which)
        except UXMultimodalRuntimeError:
            return ux_multimodal.CAPABILITY_UNSUPPORTED
        model = str(mappings.get("verifier", {}).get("model", "") or "").strip()
        if not model or "/" not in model:
            return ux_multimodal.CAPABILITY_UNSUPPORTED
        # OpenCode's session transport accepts image file parts. AutoDev currently
        # lacks authoritative per-model modality metadata, so a route that rejects
        # images is handled fail-closed as `unverifiable`. A follow-up issue tracks
        # model-specific modality discovery rather than encoding provider heuristics.
        return ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS

    def invoke_multimodal_verifier(
        self,
        repo: Path,
        *,
        prompt: str,
        attachments: tuple[Path, ...],
        contract,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> ux_multimodal.RuntimeVerificationResult:
        if self.name != "opencode":
            raise UXMultimodalRuntimeError(
                f"runtime {self.name!r} has no multimodal UX verifier adapter"
            )
        repo = repo.expanduser().resolve()
        try:
            executable = opencode_cli.resolve_opencode_cli(which=which)
            mappings = self._opencode_mappings(repo, runner=runner, which=which)
        except (opencode_cli.OpenCodeCliError, UXMultimodalRuntimeError) as exc:
            raise UXMultimodalRuntimeError(str(exc)) from exc
        model = str(mappings.get("verifier", {}).get("model", "") or "").strip()
        if not model:
            raise UXMultimodalRuntimeError(
                "cannot resolve the effective OpenCode verifier model for multimodal UX verification"
            )

        # Screenshot/reference bytes are customer/repository content. Reuse the
        # same verifier route authorization as ordinary semantic verification and
        # complete it before any image bytes are encoded or submitted.
        environment = dict(os.environ)
        try:
            policy = privacy.load_policy(repo)
            if policy.enabled:
                from automation import privacy_consent

                privacy_consent.ensure_run_consent(
                    repo,
                    mappings,
                    executable=executable,
                    runner=runner,
                )
                decision, environment = opencode_privacy_adapter.evaluate_role(
                    repo,
                    role="verifier",
                    model=model,
                    opencode_cli=executable,
                    runner=runner,
                    base_env=environment,
                )
                privacy_authorization.authorize_evaluated(repo, decision)
        except privacy.PrivacyError as exc:
            raise UXMultimodalRuntimeError(
                f"multimodal verifier privacy authorization failed: {exc}"
            ) from exc

        try:
            native = opencode_multimodal.invoke(
                executable=executable,
                repo=repo,
                model=model,
                prompt=prompt,
                attachments=attachments,
                contract=contract,
                environment=environment,
                timeout_seconds=600,
            )
        except opencode_multimodal.OpenCodeMultimodalError as exc:
            raise UXMultimodalRuntimeError(str(exc)) from exc
        return ux_multimodal.RuntimeVerificationResult(
            payload=native.value,
            runtime=self.name,
            model=model,
            capability=ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS,
            structured_output_mode="native-validated",
            schema_retry_count=native.retries,
        )

    def _opencode_mappings(
        self,
        repo: Path,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> dict[str, dict[str, str]]:
        method = getattr(self.runtime, "_resolve_mappings", None)
        try:
            if callable(method):
                value = method(repo, runner=runner, which=which)
            else:
                value = opencode_adapter_models.resolve_opencode_model_mappings(
                    repo,
                    runner=runner,
                    which=which,
                )
        except opencode_adapter_contract.OpenCodeAdapterError as exc:
            raise UXMultimodalRuntimeError(str(exc)) from exc
        if not isinstance(value, dict):
            raise UXMultimodalRuntimeError(
                "runtime returned invalid role/model mappings for multimodal verification"
            )
        return value


def adapt(runtime: object) -> RuntimeAdapter:
    return RuntimeAdapter(runtime)

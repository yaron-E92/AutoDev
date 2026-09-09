from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from automation import role_runtime_capabilities, ux_multimodal


class UXMultimodalRuntimeError(RuntimeError):
    pass


class RuntimeAdapter:
    def __init__(self, runtime: object) -> None:
        self.runtime = runtime
        self.name = str(getattr(runtime, "name", "") or "")
        self._capability_evidence: role_runtime_capabilities.ImageInputCapability | None = None
        if self.name == "opencode":
            # Keep OpenCode/provider imports lazy. Importing the optional multimodal
            # layer must not acquire the provider/workflow-stages graph for runtimes
            # that will never use it.
            from automation import opencode_model_capabilities

            opencode_model_capabilities.install()

    def multimodal_verifier_capability(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> str:
        evidence = role_runtime_capabilities.image_input_capability(
            self.runtime,
            repo,
            role="verifier",
            runner=runner,
            which=which,
        )
        self._capability_evidence = evidence
        role_runtime_capabilities.persist(repo, evidence)
        if evidence.state == role_runtime_capabilities.STATE_SUPPORTED:
            return ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS
        return ux_multimodal.CAPABILITY_UNSUPPORTED

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
        # Keep runtime/provider imports behind the adapter boundary. Besides
        # preserving provider neutrality, this prevents workflow_dispatch from
        # acquiring the OpenCode/workflow-stages import graph merely by importing
        # the optional multimodal layer.
        from automation import (
            opencode_cli,
            opencode_multimodal,
            opencode_privacy_adapter,
            privacy,
            privacy_authorization,
        )

        repo = repo.expanduser().resolve()
        if self._capability_evidence is None:
            capability = self.multimodal_verifier_capability(
                repo,
                runner=runner,
                which=which,
            )
            if capability != ux_multimodal.CAPABILITY_IMAGE_ATTACHMENTS:
                evidence = self._capability_evidence
                detail = evidence.detail if evidence is not None else "capability unavailable"
                raise UXMultimodalRuntimeError(
                    "effective verifier route has no authoritative image-input capability: "
                    + detail
                )

        try:
            mappings = self._opencode_mappings(repo, runner=runner, which=which)
        except UXMultimodalRuntimeError as exc:
            raise UXMultimodalRuntimeError(str(exc)) from exc
        model = str(mappings.get("verifier", {}).get("model", "") or "").strip()
        if not model:
            raise UXMultimodalRuntimeError(
                "cannot resolve the effective OpenCode verifier model for multimodal UX verification"
            )
        self._assert_capability_route(model)

        try:
            executable = opencode_cli.resolve_opencode_cli(which=which)
        except opencode_cli.OpenCodeCliError as exc:
            raise UXMultimodalRuntimeError(str(exc)) from exc

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

    def _assert_capability_route(self, model: str) -> None:
        evidence = self._capability_evidence
        if evidence is None:
            raise UXMultimodalRuntimeError(
                "multimodal verifier invocation has no bound image-input capability evidence"
            )
        if evidence.state != role_runtime_capabilities.STATE_SUPPORTED:
            raise UXMultimodalRuntimeError(
                "multimodal verifier invocation is blocked because image-input capability is "
                f"{evidence.state}: {evidence.detail}"
            )
        if not evidence.route:
            raise UXMultimodalRuntimeError(
                "supported image-input capability evidence is not bound to a provider/model route"
            )
        if evidence.route != model:
            raise UXMultimodalRuntimeError(
                "effective verifier route changed after image-input capability discovery; "
                f"capability was bound to {evidence.route!r} but invocation resolved {model!r}"
            )

    def _opencode_mappings(
        self,
        repo: Path,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> dict[str, dict[str, str]]:
        from automation import opencode_adapter_contract, opencode_adapter_models

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


def verify_for_semantic_stage(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
    which=None,
) -> Path:
    """Run multimodal verification only when selected visual authority exists."""

    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"
    references = ux_multimodal.selected_reference_images(repo, current)
    if not references:
        # Avoid selecting/probing any model runtime for UX-disabled or text-only
        # work. `run_verification` records an explicit not-applicable result.
        return ux_multimodal.run_verification(
            repo,
            RuntimeAdapter(object()),
            runner=runner,
            which=which,
        )

    from automation import role_runtime

    try:
        runtime, _source = role_runtime.select_runtime(repo)
    except role_runtime.RoleRuntimeError:
        # Visual authority is active, so inability to resolve the verifier route is
        # itself auditable `unverifiable` evidence rather than a silent downgrade.
        class _UnavailableRuntime:
            name = "unresolved"

        return ux_multimodal.run_verification(
            repo,
            RuntimeAdapter(_UnavailableRuntime()),
            runner=runner,
            which=which,
        )
    return ux_multimodal.run_verification(
        repo,
        adapt(runtime),
        runner=runner,
        which=which,
    )

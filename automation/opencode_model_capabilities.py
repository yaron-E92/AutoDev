from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from automation import (
    opencode_adapter_contract,
    opencode_adapter_models,
    opencode_cli,
    role_runtime_capabilities,
)


SOURCE_EXPLICIT = "opencode resolved config explicit model modalities"
SOURCE_CATALOG = "opencode models --verbose resolved model metadata"


def install() -> None:
    role_runtime_capabilities.register_resolver("opencode", resolve_image_input_capability)


def resolve_image_input_capability(
    runtime: object,
    repo: Path,
    *,
    role: str,
    runner=subprocess.run,
    which=None,
) -> role_runtime_capabilities.ImageInputCapability:
    repo = repo.expanduser().resolve()
    runtime_name = str(getattr(runtime, "name", "") or "").strip() or "opencode"
    try:
        mappings = _resolved_mappings(runtime, repo, runner=runner, which=which)
    except opencode_adapter_contract.OpenCodeAdapterError as exc:
        return _unknown(runtime_name, detail=f"cannot resolve effective role mapping: {exc}")

    route = str(mappings.get(role, {}).get("model", "") or "").strip()
    provider, separator, model = route.partition("/")
    if not separator or not provider or not model:
        return _unknown(
            runtime_name,
            detail=f"effective {role} route is not a concrete provider/model identity",
        )

    try:
        config = opencode_adapter_models.resolve_opencode_config(
            repo,
            runner=runner,
            which=which,
        )
    except opencode_adapter_contract.OpenCodeAdapterError:
        config = {}

    explicit = _explicit_modalities(config, provider, model)
    if explicit is not None:
        supported, payload = explicit
        return _evidence(
            runtime_name,
            provider,
            model,
            supported=supported,
            source=SOURCE_EXPLICIT,
            detail="effective OpenCode model has explicit input modalities in resolved configuration",
            metadata=payload,
        )

    if _custom_model_requires_modalities(config, provider, model):
        return role_runtime_capabilities.ImageInputCapability(
            state=role_runtime_capabilities.STATE_UNKNOWN,
            runtime=runtime_name,
            provider=provider,
            model=model,
            source=SOURCE_EXPLICIT,
            detail=(
                "effective custom OpenCode provider model is explicitly registered without modalities; "
                "AutoDev will not treat runtime fallback assumptions as authoritative image support"
            ),
        )

    try:
        executable = opencode_cli.resolve_opencode_cli(which=which)
    except opencode_cli.OpenCodeCliError as exc:
        return _unknown_for_route(
            runtime_name,
            provider,
            model,
            detail=f"cannot resolve OpenCode CLI for model capability discovery: {exc}",
        )

    environment = dict(os.environ)
    environment["NO_COLOR"] = "1"
    try:
        completed = runner(
            [executable, "models", provider, "--verbose"],
            cwd=repo,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return _unknown_for_route(
            runtime_name,
            provider,
            model,
            detail=f"OpenCode model capability discovery could not be launched: {exc}",
        )

    returncode = int(getattr(completed, "returncode", 1))
    if returncode != 0:
        stderr = str(getattr(completed, "stderr", "") or "").strip()
        detail = f": {stderr[:1000]}" if stderr else ""
        return _unknown_for_route(
            runtime_name,
            provider,
            model,
            detail=f"`opencode models {provider} --verbose` exited with code {returncode}{detail}",
        )

    output = str(getattr(completed, "stdout", "") or "")
    metadata = _model_metadata(output, route)
    if metadata is None:
        return _unknown_for_route(
            runtime_name,
            provider,
            model,
            detail="effective model identity was absent from OpenCode's resolved verbose model catalog",
        )

    image = _image_capability(metadata)
    if image is None:
        return role_runtime_capabilities.ImageInputCapability(
            state=role_runtime_capabilities.STATE_UNKNOWN,
            runtime=runtime_name,
            provider=provider,
            model=model,
            source=SOURCE_CATALOG,
            detail="resolved OpenCode model metadata did not contain boolean capabilities.input.image",
            metadata_sha256=_metadata_sha256(metadata),
        )
    return _evidence(
        runtime_name,
        provider,
        model,
        supported=image,
        source=SOURCE_CATALOG,
        detail="capability taken from OpenCode's resolved model object for the effective verifier route",
        metadata=metadata,
    )


def _resolved_mappings(runtime: object, repo: Path, *, runner, which=None):
    method = getattr(runtime, "_resolve_mappings", None)
    if callable(method):
        value = method(repo, runner=runner, which=which)
    else:
        value = opencode_adapter_models.resolve_opencode_model_mappings(
            repo,
            runner=runner,
            which=which,
        )
    if not isinstance(value, dict):
        raise opencode_adapter_contract.OpenCodeAdapterError(
            "runtime returned invalid role/model mappings for capability discovery"
        )
    return value


def _explicit_modalities(
    config: dict[str, object], provider: str, model: str
) -> tuple[bool, object] | None:
    entry = _configured_model_entry(config, provider, model)
    if entry is None:
        return None
    modalities = entry.get("modalities")
    if not isinstance(modalities, dict):
        return None
    inputs = modalities.get("input")
    if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
        return None
    normalized = {item.strip().casefold() for item in inputs}
    return "image" in normalized, modalities


def _custom_model_requires_modalities(
    config: dict[str, object], provider: str, model: str
) -> bool:
    raw_provider = _configured_provider(config, provider)
    if raw_provider is None:
        return False
    entry = _configured_model_entry(config, provider, model)
    if entry is None or "modalities" in entry:
        return False
    npm = raw_provider.get("npm")
    return isinstance(npm, str) and bool(npm.strip())


def _configured_provider(
    config: dict[str, object], provider: str
) -> dict[str, object] | None:
    raw_providers = config.get("provider", {})
    if not isinstance(raw_providers, dict):
        return None
    raw_provider = raw_providers.get(provider)
    return raw_provider if isinstance(raw_provider, dict) else None


def _configured_model_entry(
    config: dict[str, object], provider: str, model: str
) -> dict[str, object] | None:
    raw_provider = _configured_provider(config, provider)
    if raw_provider is None:
        return None
    raw_models = raw_provider.get("models", {})
    if not isinstance(raw_models, dict):
        return None
    raw_model = raw_models.get(model)
    return raw_model if isinstance(raw_model, dict) else None


def _model_metadata(output: str, route: str) -> dict[str, object] | None:
    normalized = output.replace("\r\n", "\n").replace("\r", "\n")
    match = re.search(rf"(?m)^{re.escape(route)}[ \t]*$", normalized)
    if match is None:
        return None
    remainder = normalized[match.end():].lstrip()
    try:
        value, _end = json.JSONDecoder().raw_decode(remainder)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _image_capability(metadata: dict[str, object]) -> bool | None:
    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    inputs = capabilities.get("input")
    if not isinstance(inputs, dict):
        return None
    image = inputs.get("image")
    return image if isinstance(image, bool) else None


def _evidence(
    runtime: str,
    provider: str,
    model: str,
    *,
    supported: bool,
    source: str,
    detail: str,
    metadata: object,
) -> role_runtime_capabilities.ImageInputCapability:
    return role_runtime_capabilities.ImageInputCapability(
        state=(
            role_runtime_capabilities.STATE_SUPPORTED
            if supported
            else role_runtime_capabilities.STATE_UNSUPPORTED
        ),
        runtime=runtime,
        provider=provider,
        model=model,
        source=source,
        detail=detail,
        metadata_sha256=_metadata_sha256(metadata),
    )


def _unknown(runtime: str, *, detail: str):
    return role_runtime_capabilities.ImageInputCapability(
        state=role_runtime_capabilities.STATE_UNKNOWN,
        runtime=runtime,
        provider="",
        model="",
        source=SOURCE_CATALOG,
        detail=detail,
    )


def _unknown_for_route(runtime: str, provider: str, model: str, *, detail: str):
    return role_runtime_capabilities.ImageInputCapability(
        state=role_runtime_capabilities.STATE_UNKNOWN,
        runtime=runtime,
        provider=provider,
        model=model,
        source=SOURCE_CATALOG,
        detail=detail,
    )


def _metadata_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

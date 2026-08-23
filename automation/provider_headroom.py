from __future__ import annotations

from automation.headroom import (
    HeadroomConfig,
    HeadroomError,
    headroom_config_from_values,
    prepare_prompt,
    proxy_headers,
)

from automation.provider_contract import (
    ModelProvider,
    ProviderError,
    ProviderResponse,
)
from automation.provider_http import (
    _OpenAICompatibleProvider,
)

class HeadroomProvider(ModelProvider):
    def __init__(
        self,
        direct_provider: _OpenAICompatibleProvider,
        proxy_provider: _OpenAICompatibleProvider,
        headroom: HeadroomConfig,
        upstream_base_url: str,
    ):
        self.direct_provider = direct_provider
        self.proxy_provider = proxy_provider
        self.headroom = headroom
        self.upstream_base_url = upstream_base_url

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        role = headroom_role_from_prompt(prompt)
        try:
            prepared = prepare_prompt(
                prompt,
                role=role,
                model=model,
                config=self.headroom,
                upstream_base_url=self.upstream_base_url,
                timeout_seconds=timeout_seconds,
            )
        except HeadroomError as exc:
            raise ProviderError(str(exc), classification="compression_failed") from exc

        if prepared.telemetry.get("status") == "compression_failed":
            direct = self.direct_provider.invoke(prompt, model=model, timeout_seconds=timeout_seconds)
            return ProviderResponse(
                direct.text,
                {**direct.telemetry, "compression": prepared.telemetry},
            )

        try:
            proxied = self.proxy_provider.invoke(
                prepared.prompt,
                model=model,
                timeout_seconds=timeout_seconds,
            )
        except ProviderError as exc:
            if self.headroom.fail_open and exc.classification == "transport_error":
                direct = self.direct_provider.invoke(prompt, model=model, timeout_seconds=timeout_seconds)
                compression = dict(prepared.telemetry)
                compression.update(
                    {
                        "status": "proxy_unavailable",
                        "warning": "Headroom proxy is unreachable; used direct upstream",
                        "fail_open_used": True,
                    }
                )
                return ProviderResponse(
                    direct.text,
                    {**direct.telemetry, "compression": compression},
                )
            raise
        return ProviderResponse(
            proxied.text,
            {**proxied.telemetry, "compression": prepared.telemetry},
        )

def headroom_role_from_prompt(prompt: str) -> str:
    marker = "AUTODEV_HEADROOM_ROLE:"
    first = prompt.find(marker)
    if first < 0:
        return ""
    value = prompt[first + len(marker):].splitlines()[0].strip().casefold()
    return value

def with_headroom_role(prompt: str, role: str) -> str:
    if not role or "AUTODEV_HEADROOM_ROLE:" in prompt:
        return prompt
    return f"AUTODEV_HEADROOM_ROLE:{role}\n{prompt}"

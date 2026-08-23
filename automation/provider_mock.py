from __future__ import annotations


from automation.provider_contract import (
    ModelProvider,
    ProviderResponse,
)

class MockProvider(ModelProvider):
    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or ["NO_CHANGES_REQUIRED\nmock response"])
        self.prompts: list[str] = []

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        self.prompts.append(prompt)
        if self.responses:
            return ProviderResponse(self.responses.pop(0), {})
        return ProviderResponse("NO_CHANGES_REQUIRED\nmock response", {})

from __future__ import annotations

import json
import os
import subprocess
from typing import Any
from urllib import error, request

from automation.provider_contract import (
    ModelProvider,
    ProviderError,
    ProviderResponse,
)
from automation.provider_requests import (
    build_chat_completions_body,
    build_responses_body,
    classify_http_status,
    http_failure_message,
    response_telemetry,
    validate_safe_headers,
)

class _OpenAICompatibleProvider(ModelProvider):
    endpoint = ""

    def __init__(
        self,
        base_url: str,
        api_key_env: str = "",
        *,
        headers: dict[str, str] | None = None,
        request_options: dict[str, object] | None = None,
        output_limit: int | None = None,
        free_only: bool = False,
        fallback_models: tuple[str, ...] = (),
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.headers = validate_safe_headers(headers or {})
        self.request_options = dict(request_options or {})
        self.output_limit = output_limit
        self.free_only = free_only
        self.fallback_models = fallback_models

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        if not self.base_url:
            raise ProviderError(
                f"{self.endpoint} provider requires a base URL",
                classification="invalid_config",
            )
        api_key = ""
        if self.api_key_env:
            api_key = os.environ.get(self.api_key_env, "")
            if not api_key:
                raise ProviderError(
                    f"environment variable is not set: {self.api_key_env}",
                    classification="missing_credentials",
                )

        body = self.build_body(model, prompt)
        headers = {"Content-Type": "application/json", **self.headers}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = request.Request(
            f"{self.base_url}/{self.endpoint}",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            raise ProviderError(
                http_failure_message(exc.code),
                classification=classify_http_status(exc.code),
                status_code=exc.code,
                retry_after=retry_after,
            ) from exc
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise ProviderError("provider request timed out", classification="timeout") from exc
        except error.URLError as exc:
            classification = "timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
            raise ProviderError("provider endpoint is unreachable", classification=classification) from exc
        except json.JSONDecodeError as exc:
            raise ProviderError("provider returned malformed JSON", classification="malformed_response") from exc

        if not isinstance(payload, dict):
            raise ProviderError("provider returned a malformed response", classification="malformed_response")
        return ProviderResponse(self.extract_text(payload), response_telemetry(payload))

    def build_body(self, model: str, prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def extract_text(self, payload: dict[str, object]) -> str:
        raise NotImplementedError

class ChatCompletionsProvider(_OpenAICompatibleProvider):
    endpoint = "chat/completions"

    def build_body(self, model: str, prompt: str) -> dict[str, Any]:
        return build_chat_completions_body(
            model,
            prompt,
            request_options=self.request_options,
            output_limit=self.output_limit,
            free_only=self.free_only,
            fallback_models=self.fallback_models,
        )

    def extract_text(self, payload: dict[str, object]) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]  # type: ignore[index]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "chat-completions response did not include assistant content",
                classification="malformed_response",
            ) from exc
        if not isinstance(content, str):
            raise ProviderError(
                "chat-completions assistant content was not text",
                classification="malformed_response",
            )
        return content

class ResponsesProvider(_OpenAICompatibleProvider):
    endpoint = "responses"

    def build_body(self, model: str, prompt: str) -> dict[str, Any]:
        return build_responses_body(
            model,
            prompt,
            request_options=self.request_options,
            output_limit=self.output_limit,
            free_only=self.free_only,
            fallback_models=self.fallback_models,
        )

    def extract_text(self, payload: dict[str, object]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text
        output = payload.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        parts.append(str(part["text"]))
            if parts:
                return "".join(parts)
        raise ProviderError(
            "responses response did not include output text",
            classification="malformed_response",
        )

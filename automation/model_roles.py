from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from automation import privacy
from automation.provider_contract import ModelConfig, ModelProvider, ProviderError, ProviderResponse

MODEL_ROLES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")

class ModelInvocationError(ProviderError):
    def __init__(self, record: dict[str, object]):
        classification = str(record.get("failure_classification", "provider_error"))
        super().__init__(
            f"{record['role']} provider call failed ({classification})",
            classification=classification,
            status_code=record.get("status_code") if isinstance(record.get("status_code"), int) else None,
        )
        self.record = record


def safe_role_metadata(configs: dict[str, ModelConfig | None]) -> dict[str, object]:
    return {
        role: config.safe_metadata() if config is not None else {"enabled": False}
        for role, config in configs.items()
    }


def invoke_model(
    provider: ModelProvider,
    config: ModelConfig,
    prompt: str,
    *,
    role: str,
    attempt: int = 0,
    repo: Path | None = None,
) -> tuple[str, dict[str, object]]:
    if role not in MODEL_ROLES:
        raise ProviderError(f"unknown model role: {role}", classification="invalid_config")
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    record: dict[str, object] = {
        "role": role,
        "attempt": attempt,
        "retry_count": attempt,
        **config.safe_metadata(),
        "started_at": started_at.isoformat(),
    }
    try:
        decision = privacy.authorize_direct_call(provider, config, role=role, repo=repo)
        record["privacy"] = decision.safe_metadata()
        if "generate" in type(provider).__dict__ and "invoke" not in type(provider).__dict__:
            response = ProviderResponse(
                provider.generate(prompt, model=config.model, timeout_seconds=config.timeout_seconds),
                {},
            )
        else:
            response = provider.invoke(prompt, model=config.model, timeout_seconds=config.timeout_seconds)
    except Exception as exc:
        classification = str(getattr(exc, "classification", "") or type(exc).__name__)
        record.update(
            {
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "status": "failure",
                "error_type": type(exc).__name__,
                "failure_classification": classification,
            }
        )
        if isinstance(exc, ProviderError):
            if exc.status_code is not None:
                record["status_code"] = exc.status_code
            if exc.retry_after:
                record["retry_after"] = exc.retry_after
        raise ModelInvocationError(record) from exc
    record.update(
        {
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "status": "success",
        }
    )
    record.update(response.telemetry)
    return response.text, record


def append_invocation_metadata(path: Path, record: dict[str, object]) -> None:
    records: list[object] = []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            records = value
    except (OSError, json.JSONDecodeError):
        pass
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")

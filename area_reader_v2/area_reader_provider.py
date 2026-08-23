from __future__ import annotations

import json
import time
from urllib import error, request
from automation.model_providers import ModelConfig, create_provider, ollama_command_for_model

from area_reader_v2.area_reader_settings import (
    OLLAMA_CHAT_URL,
)
from area_reader_v2.area_reader_verification import (
    command,
)

def call_ollama(model, prompt, num_predict):
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {
            "num_predict": num_predict,
        },
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        OLLAMA_CHAT_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.monotonic()
    try:
        with request.urlopen(http_request) as response:
            response_body = response.read().decode("utf-8")
    except error.URLError as exc:
        raise RuntimeError(f"Ollama request failed for model {model}: {exc}") from exc
    wall_seconds = time.monotonic() - started

    try:
        raw = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON for model {model}: {exc}") from exc

    return raw, wall_seconds

def duration_seconds(raw, key):
    value = raw.get(key)
    if not isinstance(value, (int, float)):
        return 0.0
    return value / 1_000_000_000

def tokens_per_sec(count, seconds):
    if not isinstance(count, (int, float)) or count <= 0 or seconds <= 0:
        return 0.0
    return count / seconds

def extract_message(raw):
    message = raw.get("message")
    if not isinstance(message, dict):
        return "", ""

    content = message.get("content")
    thinking = message.get("thinking")
    return (
        content if isinstance(content, str) else "",
        thinking if isinstance(thinking, str) else "",
    )

def build_metrics(raw, wall_seconds, response_text):
    prompt_eval_count = raw.get("prompt_eval_count", 0)
    eval_count = raw.get("eval_count", 0)
    prompt_eval_seconds = duration_seconds(raw, "prompt_eval_duration")
    eval_seconds = duration_seconds(raw, "eval_duration")

    return {
        "done_reason": raw.get("done_reason", ""),
        "wall_seconds": wall_seconds,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_tokens_per_sec": tokens_per_sec(prompt_eval_count, prompt_eval_seconds),
        "eval_count": eval_count,
        "generation_tokens_per_sec": tokens_per_sec(eval_count, eval_seconds),
        "total_duration_seconds": duration_seconds(raw, "total_duration"),
        "load_duration_seconds": duration_seconds(raw, "load_duration"),
        "response_chars": len(response_text),
    }

def model_config_from_args(args, role, model_override=None):
    model = model_override or getattr(args, f"{role}_model") or getattr(args, role)
    if not model:
        raise RuntimeError(f"{role} model is required")
    provider = getattr(args, f"{role}_provider")
    command = getattr(args, f"{role}_command")
    if provider == "command" and not command:
        command = ollama_command_for_model(str(model))
    return ModelConfig(
        provider=provider,
        model=model,
        command=command,
        base_url=getattr(args, f"{role}_base_url"),
        api_key_env=getattr(args, f"{role}_api_key_env"),
        timeout_seconds=getattr(args, f"{role}_timeout_seconds"),
    )

def call_provider(args, role, prompt, num_predict, model_override=None):
    config = model_config_from_args(args, role, model_override=model_override)
    provider = create_provider(config)
    started = time.monotonic()
    content = provider.generate(
        prompt,
        model=config.model,
        timeout_seconds=config.timeout_seconds,
    )
    wall_seconds = time.monotonic() - started
    return {
        "message": {
            "content": content,
            "thinking": "",
        },
        "done_reason": "stop",
        "provider": config.provider,
        "model": config.model,
        "eval_count": 0,
        "prompt_eval_count": 0,
    }, wall_seconds

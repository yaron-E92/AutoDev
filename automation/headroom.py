from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Callable
from urllib import error, parse, request


DEFAULT_PROXY_URL = "http://127.0.0.1:8787/v1"
SUPPORTED_MODE = "lossless"


class HeadroomError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeadroomConfig:
    enabled: bool = False
    proxy_url: str = DEFAULT_PROXY_URL
    fail_open: bool = True
    mode: str = SUPPORTED_MODE
    output_shaping: bool = False

    def safe_metadata(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "proxy_url": sanitize_url(self.proxy_url),
            "fail_open": self.fail_open,
            "mode": self.mode,
            "output_shaping": self.output_shaping,
        }


@dataclass(frozen=True)
class HeadroomPromptResult:
    prompt: str
    telemetry: dict[str, object]


def resolve_headroom_values(file_config: dict[str, object], role: str) -> dict[str, object]:
    raw = file_config.get("headroom", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HeadroomError("headroom configuration must be an object")
    roles = raw.get("roles", {})
    if roles is None:
        roles = {}
    if not isinstance(roles, dict):
        raise HeadroomError("headroom.roles must be an object")
    override = roles.get(role, {})
    if override is None:
        override = {}
    if not isinstance(override, dict):
        raise HeadroomError(f"headroom role override must be an object: {role}")

    values = {key: value for key, value in raw.items() if key != "roles"}
    values.update(override)
    if role == "verifier" and "enabled" not in override:
        values["enabled"] = False
    return values


def headroom_config_from_values(value: object) -> HeadroomConfig:
    if value in (None, {}):
        return HeadroomConfig()
    if not isinstance(value, dict):
        raise HeadroomError("headroom role configuration must be an object")
    enabled = _bool_value(value.get("enabled", False), "headroom.enabled")
    fail_open = _bool_value(value.get("fail_open", True), "headroom.fail_open")
    output_shaping = _bool_value(value.get("output_shaping", False), "headroom.output_shaping")
    proxy_url = str(value.get("proxy_url", DEFAULT_PROXY_URL)).strip().rstrip("/")
    mode = str(value.get("mode", SUPPORTED_MODE)).strip().casefold()
    if enabled and not proxy_url:
        raise HeadroomError("headroom.proxy_url is required when Headroom is enabled")
    if mode != SUPPORTED_MODE:
        raise HeadroomError("headroom.mode currently supports only lossless")
    if output_shaping:
        raise HeadroomError("headroom.output_shaping must remain false for AutoDev")
    return HeadroomConfig(enabled, proxy_url, fail_open, mode, output_shaping)


def prepare_prompt(
    prompt: str,
    *,
    role: str,
    model: str,
    config: HeadroomConfig,
    upstream_base_url: str,
    timeout_seconds: int,
    urlopen: Callable[..., object] = request.urlopen,
) -> HeadroomPromptResult:
    original_hash = prompt_hash(prompt)
    base = {
        "enabled": config.enabled,
        "mode": config.mode,
        "output_shaping": config.output_shaping,
        "proxy_url": sanitize_url(config.proxy_url),
        "upstream_base_url": sanitize_url(upstream_base_url),
        "fail_open_used": False,
        "original_prompt_sha256": original_hash,
        "effective_prompt_sha256": original_hash,
        "characters_before": len(prompt),
        "characters_after": len(prompt),
    }
    if not config.enabled:
        return HeadroomPromptResult(prompt, {**base, "status": "disabled", "evidence_sections": 0})

    ranges = compressible_ranges(prompt, role)
    if not ranges:
        return HeadroomPromptResult(prompt, {**base, "status": "no_eligible_evidence", "evidence_sections": 0})

    evidence = [prompt[start:end] for start, end in ranges]
    started = time.monotonic()
    try:
        compressed, metrics = _compress_messages(
            config.proxy_url,
            evidence,
            model,
            timeout_seconds,
            urlopen,
        )
    except HeadroomError as exc:
        telemetry = {
            **base,
            "status": "compression_failed",
            "evidence_sections": len(ranges),
            "elapsed_compression_seconds": round(time.monotonic() - started, 6),
            "warning": str(exc),
            "fail_open_used": config.fail_open,
        }
        if config.fail_open:
            return HeadroomPromptResult(prompt, telemetry)
        raise

    effective = prompt
    for (start, end), replacement in reversed(list(zip(ranges, compressed))):
        effective = effective[:start] + replacement + effective[end:]
    telemetry = {
        **base,
        "status": "compressed",
        "evidence_sections": len(ranges),
        "elapsed_compression_seconds": round(time.monotonic() - started, 6),
        "effective_prompt_sha256": prompt_hash(effective),
        "characters_after": len(effective),
        "evidence_characters_before": sum(len(item) for item in evidence),
        "evidence_characters_after": sum(len(item) for item in compressed),
        **metrics,
    }
    return HeadroomPromptResult(effective, telemetry)


def proxy_headers(upstream_base_url: str) -> dict[str, str]:
    return {
        "X-Headroom-Base-Url": upstream_base_url.rstrip("/"),
        "X-Headroom-Bypass": "true",
    }


def compressible_ranges(prompt: str, role: str) -> list[tuple[int, int]]:
    role = role or infer_role(prompt)
    pairs: list[tuple[str, str | None]] = []
    if role == "reader":
        pairs = [("Area bundle metadata:\n", None)]
    elif role == "synthesizer":
        pairs = [("Routed areas:\n", None)]
    elif role == "implementer":
        if "You are the Implementer editing this repository." in prompt:
            pairs = [("Planner output:\n", "\n\nIssue:\n")]
        else:
            pairs = [
                ("Synthesized handoff:\n", "\n\nCoder plan:"),
                ("Coder plan:\n", "\n\nRecommended command groups JSON:"),
                ("Recommended command groups JSON:\n", "\n\nRepository constraints:"),
            ]
    elif role == "fixer":
        if (
            "Semantic-only repair evidence:" in prompt
            and "{~{RepairBrief}~}" not in prompt
            and "{~{ChangedFiles}~}" not in prompt
            and "{~{Diff}~}" not in prompt
        ):
            pairs = [
                ("Implementation plan:\n", "\n\nVerifier result:"),
                ("Changed files:\n", "\n\nCurrent diff:"),
                ("Current diff:\n", "\n\nSemantic repair output contract:"),
            ]
        elif "You are the Debugger for this repository." in prompt:
            pairs = [
                ("Error / bug / failed behavior:\n", "\n\nRelevant issue:\n"),
            ]
        elif "You are the CI Debugger for this repository." in prompt:
            pairs = [
                ("CI summary / failure information:\n", "\n\nRelevant issue:\n"),
                ("Planner output:\n", "\n\nOutput contract:\n"),
            ]
        elif "You are the Fixer correcting verifier gaps." in prompt:
            pairs = [
                ("Implementation plan:\n", "\n\nVerifier result:"),
                ("Verifier result:\n", "\n\nSemantic-only repair evidence:"),
            ]
        else:
            pairs = [("Synthesized handoff:\n", "\n\nOutput only:")]
    elif role == "planner":
        if "Area-reader routed areas:" in prompt:
            pairs = [("Area-reader routed areas:\n", "\n\nAutomation context:")]
        elif "You are the coder model in an area-based local LLM benchmark." in prompt:
            pairs = [("Synthesized handoff:\n", None)]
    elif role == "verifier" and "Output contract:" in prompt:
        pairs = [
            ("Synthesized repository handoff:\n", "\n\nImplementation plan:"),
            ("Implementation plan:\n", "\n\nChanged files:"),
            ("Changed files:\n", "\n\nCurrent diff:"),
            ("Current diff:\n", "\n\nDeterministic verification evidence:"),
            ("Deterministic verification evidence:\n", "\n\nCross-file regression evidence:"),
            ("Cross-file regression evidence:\n", "\n\nRelevant uncertainty or skipped-check notes:"),
            ("Relevant uncertainty or skipped-check notes:\n", "\n\nOutput contract:"),
        ]
    return _find_ranges(prompt, pairs)


def infer_role(prompt: str) -> str:
    if "You are the area reader model for area:" in prompt:
        return "reader"
    if "You are the synthesis reader model in an area-based local LLM benchmark." in prompt:
        return "synthesizer"
    if "You are the coder model for AutoDev." in prompt or "You are the Implementer editing this repository." in prompt:
        return "implementer"
    if (
        "You are the fixer model for AutoDev." in prompt
        or "You are the Fixer correcting verifier gaps." in prompt
        or "You are the Debugger for this repository." in prompt
        or "You are the CI Debugger for this repository." in prompt
    ):
        return "fixer"
    if "You are the Planner for this repository." in prompt or "You are the coder model in an area-based local LLM benchmark." in prompt:
        return "planner"
    if "You are the independent Verifier for this repository." in prompt:
        return "verifier"
    return ""


def sanitize_url(value: str) -> str:
    if not value:
        return ""
    parts = parse.urlsplit(value)
    hostname = parts.hostname or ""
    if parts.port is not None:
        hostname += f":{parts.port}"
    return parse.urlunsplit((parts.scheme, hostname, parts.path, "", ""))


def prompt_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _compress_messages(
    proxy_url: str,
    evidence: list[str],
    model: str,
    timeout_seconds: int,
    urlopen: Callable[..., object],
) -> tuple[list[str], dict[str, object]]:
    body = {
        "messages": [{"role": "user", "content": item} for item in evidence],
        "model": model,
    }
    req = request.Request(
        f"{proxy_url.rstrip('/')}/compress",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as response:  # type: ignore[attr-defined]
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise HeadroomError(f"Headroom compression failed (HTTP {exc.code})") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise HeadroomError("Headroom compression endpoint is unavailable") from exc
    except json.JSONDecodeError as exc:
        raise HeadroomError("Headroom compression returned malformed JSON") from exc

    if not isinstance(payload, dict):
        raise HeadroomError("Headroom compression returned a malformed response")
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != len(evidence):
        raise HeadroomError("Headroom compression did not preserve evidence section count")
    compressed: list[str] = []
    for item in messages:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise HeadroomError("Headroom compression returned malformed message content")
        compressed.append(str(item["content"]))

    metrics: dict[str, object] = {}
    for key in ("tokens_before", "tokens_after", "tokens_saved", "compression_ratio"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[key] = value
    transforms = payload.get("transforms_applied")
    if isinstance(transforms, list) and all(isinstance(item, str) for item in transforms):
        metrics["transforms_applied"] = transforms
    return compressed, metrics


def _find_ranges(prompt: str, pairs: list[tuple[str, str | None]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start_marker, end_marker in pairs:
        marker_index = prompt.find(start_marker)
        if marker_index < 0:
            continue
        start = marker_index + len(start_marker)
        end = len(prompt) if end_marker is None else prompt.find(end_marker, start)
        if end < 0 or end <= start:
            continue
        ranges.append((start, end))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if previous[1] > current[0]:
            raise HeadroomError("Headroom evidence sections overlap")
    return ranges


def _bool_value(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise HeadroomError(f"{label} must be boolean")
    return value

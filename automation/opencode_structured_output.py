from __future__ import annotations

import base64
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from automation import role_output_contract


SERVER_START_TIMEOUT_SECONDS = 5.0
HEALTH_POLL_SECONDS = 0.05
MAX_ERROR_CHARS = 1000


class OpenCodeStructuredOutputError(RuntimeError):
    pass


class StructuredOutputUnavailable(OpenCodeStructuredOutputError):
    """The installed OpenCode runtime does not expose the required API contract."""


class StructuredOutputExhausted(OpenCodeStructuredOutputError):
    def __init__(self, message: str, *, retries: int = 0) -> None:
        super().__init__(message)
        self.retries = max(0, int(retries))


class StructuredOutputTransportError(OpenCodeStructuredOutputError):
    """The supported native path failed as a runtime/transport operation."""


@dataclass(frozen=True)
class NativeStructuredOutput:
    value: dict[str, object]
    retries: int = 0


def invoke(
    *,
    executable: str,
    repo: Path,
    role: str,
    model: str,
    prompt: str,
    contract: role_output_contract.RoleOutputContract,
    environment: dict[str, str],
    timeout_seconds: int,
    popen: Callable[..., object] = subprocess.Popen,
    urlopen: Callable[..., object] = urllib.request.urlopen,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> NativeStructuredOutput:
    """Invoke OpenCode through its documented local server/session API.

    `opencode run --format json` is deliberately not used here: that flag formats
    CLI events and does not constrain model output. The native path starts the
    documented headless server and sends a session prompt with a JSON-schema
    `format` object, matching the OpenCode SDK contract.
    """

    repo = repo.expanduser().resolve()
    provider_id, model_id = _split_model(model)
    port = _ephemeral_port()
    base_url = f"http://127.0.0.1:{port}"
    process = None
    session_id = ""
    started = monotonic()
    try:
        try:
            process = popen(
                [
                    executable,
                    "serve",
                    "--hostname",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=repo,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise StructuredOutputTransportError(
                f"OpenCode structured-output server could not be launched: {exc}"
            ) from exc

        _wait_for_health(
            process,
            base_url,
            environment,
            urlopen=urlopen,
            monotonic=monotonic,
            sleep=sleep,
        )
        session = _request_json(
            base_url,
            "/session",
            method="POST",
            payload={"title": f"AutoDev {role} structured output"},
            environment=environment,
            timeout=_remaining_timeout(started, timeout_seconds, monotonic),
            urlopen=urlopen,
        )
        session_id = str(session.get("id", "") or "") if isinstance(session, dict) else ""
        if not session_id:
            raise StructuredOutputTransportError(
                "OpenCode structured-output session response did not contain an id"
            )

        body: dict[str, object] = {
            "model": {"providerID": provider_id, "modelID": model_id},
            "agent": f"autodev-{role}",
            "parts": [
                {
                    "type": "text",
                    "text": prompt + role_output_contract.native_prompt_suffix(contract),
                }
            ],
            "format": {
                "type": "json_schema",
                "schema": contract.schema(),
                "retryCount": contract.native_retry_count,
            },
        }
        try:
            response = _request_json(
                base_url,
                f"/session/{session_id}/message",
                method="POST",
                payload=body,
                environment=environment,
                timeout=_remaining_timeout(started, timeout_seconds, monotonic),
                urlopen=urlopen,
            )
        except StructuredOutputTransportError as exc:
            if _looks_like_unsupported_format(str(exc)):
                raise StructuredOutputUnavailable(
                    "installed OpenCode server does not accept JSON-schema session format"
                ) from exc
            raise

        return parse_prompt_response(response)
    finally:
        if session_id:
            try:
                _request_json(
                    base_url,
                    f"/session/{session_id}",
                    method="DELETE",
                    payload=None,
                    environment=environment,
                    timeout=2.0,
                    urlopen=urlopen,
                )
            except OpenCodeStructuredOutputError:
                pass
        _stop_process(process)


def parse_prompt_response(value: object) -> NativeStructuredOutput:
    if not isinstance(value, dict):
        raise StructuredOutputTransportError(
            "OpenCode structured-output prompt returned an unexpected response"
        )
    info = value.get("info", {})
    if not isinstance(info, dict):
        raise StructuredOutputTransportError(
            "OpenCode structured-output prompt response is missing message info"
        )
    error = info.get("error", {})
    if isinstance(error, dict) and error:
        name = str(error.get("name", "") or "")
        message = _bounded(str(error.get("message", "") or name or "structured output failed"))
        if name == "StructuredOutputError":
            retries = int(error.get("retries", 0) or 0)
            raise StructuredOutputExhausted(message, retries=retries)
        raise StructuredOutputTransportError(
            f"OpenCode structured-output prompt failed: {message}"
        )
    payload = info.get("structured_output")
    if payload is None:
        # OpenCode SDK documents `info.structured_output`; absence on an otherwise
        # successful message means this server/model did not execute the requested
        # structured-output contract rather than giving AutoDev permission to trust
        # free-form text as native output.
        raise StructuredOutputUnavailable(
            "OpenCode response did not expose info.structured_output"
        )
    if not isinstance(payload, dict):
        raise StructuredOutputTransportError(
            "OpenCode structured_output did not contain a JSON object"
        )
    return NativeStructuredOutput(dict(payload), retries=0)


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str,
    payload: object | None,
    environment: dict[str, str],
    timeout: float,
    urlopen: Callable[..., object],
) -> object:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    password = str(environment.get("OPENCODE_SERVER_PASSWORD", "") or "")
    if password:
        username = str(environment.get("OPENCODE_SERVER_USERNAME", "") or "opencode")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(
        base_url + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        response = urlopen(request, timeout=max(0.1, float(timeout)))
        raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise StructuredOutputTransportError(
            f"OpenCode server HTTP {exc.code}: {_bounded(detail)}"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StructuredOutputTransportError(
            f"OpenCode structured-output server request failed: {_bounded(str(exc))}"
        ) from exc
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StructuredOutputTransportError(
            "OpenCode structured-output server returned invalid JSON"
        ) from exc


def _wait_for_health(
    process: object,
    base_url: str,
    environment: dict[str, str],
    *,
    urlopen: Callable[..., object],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    deadline = monotonic() + SERVER_START_TIMEOUT_SECONDS
    while monotonic() < deadline:
        poll = getattr(process, "poll", None)
        if callable(poll) and poll() is not None:
            stderr = _process_stderr(process)
            if _looks_like_unknown_serve(stderr):
                raise StructuredOutputUnavailable(
                    "installed OpenCode CLI does not support `opencode serve`"
                )
            raise StructuredOutputTransportError(
                "OpenCode structured-output server exited during startup"
                + (f": {_bounded(stderr)}" if stderr else "")
            )
        try:
            health = _request_json(
                base_url,
                "/global/health",
                method="GET",
                payload=None,
                environment=environment,
                timeout=0.5,
                urlopen=urlopen,
            )
            if isinstance(health, dict) and health.get("healthy") is True:
                return
        except StructuredOutputTransportError:
            pass
        sleep(HEALTH_POLL_SECONDS)
    raise StructuredOutputTransportError(
        "OpenCode structured-output server did not become healthy within startup timeout"
    )


def _split_model(model: str) -> tuple[str, str]:
    provider, separator, model_id = str(model or "").strip().partition("/")
    if not separator or not provider or not model_id:
        raise StructuredOutputUnavailable(
            "native structured output requires an effective provider/model mapping"
        )
    return provider, model_id


def _remaining_timeout(
    started: float,
    timeout_seconds: int,
    monotonic: Callable[[], float],
) -> float:
    remaining = float(timeout_seconds) - (monotonic() - started)
    if remaining <= 0:
        raise StructuredOutputTransportError(
            "OpenCode structured-output invocation exhausted its role timeout"
        )
    return remaining


def _ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _stop_process(process: object | None) -> None:
    if process is None:
        return
    poll = getattr(process, "poll", None)
    if callable(poll) and poll() is not None:
        return
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        try:
            terminate()
        except OSError:
            return
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            wait(timeout=2)
            return
        except (subprocess.TimeoutExpired, OSError):
            pass
    kill = getattr(process, "kill", None)
    if callable(kill):
        try:
            kill()
        except OSError:
            pass


def _process_stderr(process: object) -> str:
    stream = getattr(process, "stderr", None)
    if stream is None:
        return ""
    try:
        return str(stream.read() or "")
    except (OSError, ValueError):
        return ""


def _looks_like_unsupported_format(value: str) -> bool:
    lowered = str(value or "").casefold()
    return (
        "format" in lowered
        and any(token in lowered for token in ("unknown", "unrecognized", "invalid", "unexpected"))
    ) or "structured_output" in lowered and "unknown" in lowered


def _looks_like_unknown_serve(value: str) -> bool:
    lowered = str(value or "").casefold()
    return "serve" in lowered and any(
        token in lowered for token in ("unknown", "unrecognized", "invalid command")
    )


def _bounded(value: str) -> str:
    text = " ".join(str(value or "").split())
    return text[:MAX_ERROR_CHARS]

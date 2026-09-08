from __future__ import annotations

import base64
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, TextIO

from automation import opencode_structured_output, role_output_contract, ux_capture


class OpenCodeMultimodalError(RuntimeError):
    pass


def invoke(
    *,
    executable: str,
    repo: Path,
    model: str,
    prompt: str,
    attachments: tuple[Path, ...],
    contract: role_output_contract.RoleOutputContract,
    environment: dict[str, str],
    timeout_seconds: int,
    popen: Callable[..., object] = subprocess.Popen,
    urlopen=None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> opencode_structured_output.NativeStructuredOutput:
    """Invoke the OpenCode verifier with explicit image file parts and JSON schema.

    Reference and implementation images are supplied only by AutoDev's bounded UX
    verification seam. Arbitrary repository files and desktop content never pass
    through this function.
    """

    if not attachments:
        raise OpenCodeMultimodalError("multimodal verifier requires at least one image attachment")
    if len(attachments) > 64:
        raise OpenCodeMultimodalError("multimodal verifier attachment count exceeds the bounded limit")
    if urlopen is None:
        import urllib.request

        urlopen = urllib.request.urlopen

    repo = repo.expanduser().resolve()
    provider_id, model_id = opencode_structured_output._split_model(model)
    parts: list[dict[str, object]] = [
        {
            "type": "text",
            "text": prompt + role_output_contract.native_prompt_suffix(contract),
        }
    ]
    for index, attachment in enumerate(attachments, start=1):
        path = attachment.expanduser().resolve()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise OpenCodeMultimodalError(
                f"multimodal image attachment {index} is unreadable: {path}"
            ) from exc
        if not data or len(data) > ux_capture.MAX_IMAGE_BYTES:
            raise OpenCodeMultimodalError(
                f"multimodal image attachment {index} has invalid size: {path.name}"
            )
        mime = ux_capture.image_mime(data)
        if not mime:
            raise OpenCodeMultimodalError(
                f"multimodal image attachment {index} is not a supported image: {path.name}"
            )
        encoded = base64.b64encode(data).decode("ascii")
        parts.append(
            {
                "type": "file",
                "mime": mime,
                "filename": path.name,
                "url": f"data:{mime};base64,{encoded}",
            }
        )

    port = opencode_structured_output._ephemeral_port()
    base_url = f"http://127.0.0.1:{port}"
    process = None
    stderr_stream: TextIO | None = None
    session_id = ""
    started = monotonic()
    try:
        stderr_stream = tempfile.TemporaryFile(
            mode="w+t",
            encoding="utf-8",
            errors="replace",
        )
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
                stderr=stderr_stream,
            )
        except OSError as exc:
            raise OpenCodeMultimodalError(
                f"OpenCode multimodal server could not be launched: {exc}"
            ) from exc

        try:
            opencode_structured_output._wait_for_health(
                process,
                stderr_stream,
                base_url,
                environment,
                urlopen=urlopen,
                monotonic=monotonic,
                sleep=sleep,
            )
            session = opencode_structured_output._request_json(
                base_url,
                "/session",
                method="POST",
                payload={"title": "AutoDev multimodal UX verification"},
                environment=environment,
                timeout=opencode_structured_output._remaining_timeout(
                    started, timeout_seconds, monotonic
                ),
                urlopen=urlopen,
            )
            session_id = str(session.get("id", "") or "") if isinstance(session, dict) else ""
            if not session_id:
                raise OpenCodeMultimodalError(
                    "OpenCode multimodal session response did not contain an id"
                )
            response = opencode_structured_output._request_json(
                base_url,
                f"/session/{session_id}/message",
                method="POST",
                payload={
                    "model": {"providerID": provider_id, "modelID": model_id},
                    "agent": "autodev-verifier",
                    "parts": parts,
                    "format": {
                        "type": "json_schema",
                        "schema": contract.schema(),
                        "retryCount": contract.native_retry_count,
                    },
                },
                environment=environment,
                timeout=opencode_structured_output._remaining_timeout(
                    started, timeout_seconds, monotonic
                ),
                urlopen=urlopen,
            )
            return opencode_structured_output.parse_prompt_response(response)
        except opencode_structured_output.OpenCodeStructuredOutputError as exc:
            raise OpenCodeMultimodalError(str(exc)) from exc
    finally:
        if session_id:
            try:
                opencode_structured_output._request_json(
                    base_url,
                    f"/session/{session_id}",
                    method="DELETE",
                    payload=None,
                    environment=environment,
                    timeout=2.0,
                    urlopen=urlopen,
                )
            except opencode_structured_output.OpenCodeStructuredOutputError:
                pass
        opencode_structured_output._stop_process(process)
        if stderr_stream is not None:
            stderr_stream.close()

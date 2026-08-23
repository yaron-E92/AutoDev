from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from automation.provider_contract import (
    ModelProvider,
    ProviderError,
    ProviderResponse,
)

class CommandProvider(ModelProvider):
    def __init__(self, command: str):
        self.command = command

    def invoke(self, prompt: str, *, model: str, timeout_seconds: int) -> ProviderResponse:
        if not self.command:
            raise ProviderError("command provider requires a command", classification="invalid_config")
        try:
            argv = shlex.split(self.command, posix=os.name != "nt")
        except ValueError as exc:
            raise ProviderError("command provider command is malformed", classification="invalid_config") from exc
        if not argv:
            raise ProviderError("command provider command is empty", classification="invalid_config")

        prompt_path: Path | None = None
        try:
            if "{prompt}" in self.command or "{prompt_file}" in self.command:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                    handle.write(prompt)
                    prompt_path = Path(handle.name)
                rendered = self.command.replace(
                    "{prompt_file}", quote_shell_argument(str(prompt_path))
                ).replace("{prompt}", quote_shell_argument(prompt))
                completed = subprocess.run(
                    rendered,
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            else:
                completed = subprocess.run(
                    argv,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("command provider timed out", classification="timeout") from exc
        except OSError as exc:
            raise ProviderError(
                f"command executable is unavailable: {argv[0]}",
                classification="command_unavailable",
            ) from exc
        finally:
            if prompt_path is not None:
                prompt_path.unlink(missing_ok=True)

        if completed.returncode != 0:
            raise ProviderError(
                f"command provider exited with {completed.returncode}: {argv[0]}",
                classification="command_failed",
            )
        return ProviderResponse(completed.stdout, {"returncode": completed.returncode})

def quote_shell_argument(value: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)

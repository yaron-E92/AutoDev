from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from automation import external_error_sanitizer, ux_resolver


MIN_ORAS_VERSION = (1, 2, 0)
_VERSION_RE = re.compile(r"(?im)^Version:\s*(\d+)\.(\d+)\.(\d+)(?:[-+][^\s]+)?\s*$")


@dataclass(frozen=True)
class OrasToolStatus:
    available: bool
    path: str = ""
    version: str = ""
    supported: bool = False
    reason: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


class OrasClient:
    def __init__(
        self,
        *,
        executable: str = "",
        runner: Callable[..., object] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        environ: Mapping[str, str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._explicit_executable = executable.strip()
        self._runner = runner
        self._which = which
        self._environ = dict(os.environ if environ is None else environ)
        self._timeout_seconds = timeout_seconds
        self._status: OrasToolStatus | None = None

    def _path(self) -> str:
        if self._explicit_executable:
            return self._explicit_executable
        return str(self._which("oras") or "")

    def _raw(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        input_text: str | None = None,
    ):
        try:
            process_env = dict(self._environ)
            for secret_name in (
                "AUTODEV_OCI_PASSWORD",
                "AUTODEV_OCI_TOKEN",
                "GITHUB_TOKEN",
            ):
                process_env.pop(secret_name, None)
            return self._runner(
                argv,
                cwd=cwd,
                input=input_text,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=self._timeout_seconds,
                env=process_env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ux_resolver.UXResolutionError(
                "ORAS command timed out",
                classification=ux_resolver.FAILURE_TRANSPORT,
                resolver_kind="oci",
            ) from exc
        except OSError as exc:
            raise ux_resolver.UXResolutionError(
                f"ORAS executable is unavailable: {argv[0] if argv else 'oras'}",
                classification=ux_resolver.FAILURE_TOOL,
                resolver_kind="oci",
            ) from exc

    @staticmethod
    def _output(completed: object) -> tuple[int, str, str]:
        return (
            int(getattr(completed, "returncode", 1) or 0),
            str(getattr(completed, "stdout", "") or ""),
            str(getattr(completed, "stderr", "") or ""),
        )

    def status(self, *, refresh: bool = False) -> OrasToolStatus:
        if self._status is not None and not refresh:
            return self._status
        path = self._path()
        if not path:
            self._status = OrasToolStatus(
                available=False,
                supported=False,
                reason=(
                    "oras is not on PATH; install ORAS CLI >= "
                    + ".".join(str(item) for item in MIN_ORAS_VERSION)
                ),
            )
            return self._status

        completed = self._raw([path, "version"])
        code, stdout, stderr = self._output(completed)
        text = stdout + "\n" + stderr
        if code != 0:
            self._status = OrasToolStatus(
                available=True,
                path=path,
                supported=False,
                reason="oras version failed: "
                + external_error_sanitizer.sanitize_external_text(text),
            )
            return self._status
        match = _VERSION_RE.search(text)
        if not match:
            self._status = OrasToolStatus(
                available=True,
                path=path,
                supported=False,
                reason="cannot parse oras version output",
            )
            return self._status
        version_tuple = tuple(int(match.group(index)) for index in range(1, 4))
        version_text = ".".join(str(item) for item in version_tuple)
        if version_tuple < MIN_ORAS_VERSION:
            self._status = OrasToolStatus(
                available=True,
                path=path,
                version=version_text,
                supported=False,
                reason=(
                    f"ORAS {version_text} is unsupported; require >= "
                    + ".".join(str(item) for item in MIN_ORAS_VERSION)
                ),
            )
            return self._status

        capabilities = (
            (["resolve", "--help"], ()),
            (["manifest", "fetch", "--help"], ("--output",)),
            (["blob", "fetch", "--help"], ("--output",)),
            (["push", "--help"], ("--artifact-type", "--format")),
        )
        for arguments, required in capabilities:
            result = self._raw([path, *arguments])
            help_code, help_out, help_err = self._output(result)
            combined = help_out + "\n" + help_err
            if help_code != 0 or any(token not in combined for token in required):
                command = " ".join(arguments[:-1])
                self._status = OrasToolStatus(
                    available=True,
                    path=path,
                    version=version_text,
                    supported=False,
                    reason=f"ORAS {version_text} lacks required {command} capability",
                )
                return self._status

        self._status = OrasToolStatus(
            available=True,
            path=path,
            version=version_text,
            supported=True,
        )
        return self._status

    def require_tool(self) -> OrasToolStatus:
        status = self.status()
        if not status.available:
            raise ux_resolver.UXResolutionError(
                status.reason,
                classification=ux_resolver.FAILURE_TOOL,
                resolver_kind="oci",
            )
        if not status.supported:
            raise ux_resolver.UXResolutionError(
                status.reason,
                classification=ux_resolver.FAILURE_TOOL_VERSION,
                resolver_kind="oci",
            )
        return status

    def _credentials(self, registry: str) -> tuple[list[str], str, str]:
        username = self._environ.get("AUTODEV_OCI_USERNAME", "").strip()
        password = self._environ.get("AUTODEV_OCI_PASSWORD", "")
        token = self._environ.get("AUTODEV_OCI_TOKEN", "")
        github_token = self._environ.get("GITHUB_TOKEN", "")

        if password:
            if not username:
                raise ux_resolver.UXResolutionError(
                    "AUTODEV_OCI_PASSWORD requires AUTODEV_OCI_USERNAME",
                    classification=ux_resolver.FAILURE_AUTH,
                    resolver_kind="oci",
                )
            return ["--username", username, "--password-stdin"], password + "\n", "environment-password"

        if token:
            if username:
                return ["--username", username, "--password-stdin"], token + "\n", "environment-token"
            if registry.casefold() == "ghcr.io":
                actor = self._environ.get("GITHUB_ACTOR", "").strip()
                if not actor:
                    raise ux_resolver.UXResolutionError(
                        "AUTODEV_OCI_TOKEN for ghcr.io requires AUTODEV_OCI_USERNAME or GITHUB_ACTOR",
                        classification=ux_resolver.FAILURE_AUTH,
                        resolver_kind="oci",
                    )
                return ["--username", actor, "--password-stdin"], token + "\n", "environment-token"
            return ["--identity-token-stdin"], token + "\n", "environment-identity-token"

        if github_token and registry.casefold() == "ghcr.io":
            actor = username or self._environ.get("GITHUB_ACTOR", "").strip()
            if not actor:
                raise ux_resolver.UXResolutionError(
                    "GITHUB_TOKEN for ghcr.io requires AUTODEV_OCI_USERNAME or GITHUB_ACTOR",
                    classification=ux_resolver.FAILURE_AUTH,
                    resolver_kind="oci",
                )
            return ["--username", actor, "--password-stdin"], github_token + "\n", "github-token"

        return [], "", "credential-store"

    @staticmethod
    def _failure_classification(text: str) -> str:
        lowered = text.casefold()
        if any(
            marker in lowered
            for marker in (
                "unauthorized",
                "authentication required",
                "access denied",
                "permission denied",
                "forbidden",
                "status code: 401",
                "status code: 403",
                " 401 ",
                " 403 ",
            )
        ):
            return ux_resolver.FAILURE_AUTH
        if any(
            marker in lowered
            for marker in (
                "not found",
                "manifest unknown",
                "name unknown",
                "repository unknown",
                "status code: 404",
                " 404 ",
            )
        ):
            return ux_resolver.FAILURE_NOT_FOUND
        return ux_resolver.FAILURE_TRANSPORT

    def invoke(
        self,
        prefix: list[str],
        positionals: list[str],
        *,
        registry: str,
        cwd: Path | None = None,
    ) -> object:
        status = self.require_tool()
        auth_args, secret_input, _source = self._credentials(registry)
        argv = [status.path, *prefix, *auth_args, *positionals]
        completed = self._raw(
            argv,
            cwd=cwd,
            input_text=secret_input or None,
        )
        code, stdout, stderr = self._output(completed)
        if code == 0:
            return completed
        raw = stderr or stdout or f"ORAS exited with code {code}"
        if secret_input:
            raw = raw.replace(secret_input.strip(), "<redacted>")
        safe = external_error_sanitizer.sanitize_external_text(raw)
        raise ux_resolver.UXResolutionError(
            f"ORAS command failed ({code}): {safe or 'no diagnostic output'}",
            classification=self._failure_classification(safe),
            resolver_kind="oci",
        )

    def credential_source(self, registry: str) -> str:
        _args, _secret, source = self._credentials(registry)
        return source

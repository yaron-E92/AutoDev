from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

from automation import (
    opencode_adapter_assets,
    opencode_adapter_contract,
    opencode_cli,
    opencode_role_runtime,
    role_runtime,
    run_manifest,
)


class OpenCodeSchedulerRoleRuntime(opencode_role_runtime.OpenCodeRoleRuntime):
    """OpenCode adapter for the provider-neutral scheduler lifecycle."""

    def __init__(self) -> None:
        super().__init__()
        self._scheduler_environment: dict[str, str] = {}
        self._scheduler_executable = ""

    def role_snapshots(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> dict[str, object]:
        snapshots = super().role_snapshots(repo, runner=runner, which=which)
        try:
            state = role_runtime.scheduler_runtime_state(repo)
        except role_runtime.RoleRuntimeError:
            state = None
        if state is None or state.name != self.name:
            return snapshots
        for snapshot in snapshots.values():
            if not isinstance(snapshot, dict):
                continue
            base_fingerprint = str(snapshot.get("fingerprint", "") or "")
            safe = snapshot.get("safe_metadata", {})
            safe_value = dict(safe) if isinstance(safe, dict) else {}
            safe_value["scheduler_runtime"] = {
                "fingerprint": state.fingerprint,
                "identity": dict(state.identity),
            }
            snapshot["safe_metadata"] = safe_value
            snapshot["fingerprint"] = run_manifest.hash_json(
                {
                    "base_role_fingerprint": base_fingerprint,
                    "scheduler_runtime_fingerprint": state.fingerprint,
                }
            )
        return snapshots

    def provision_scheduler_worker(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> None:
        super().provision_scheduler_worker(repo, runner=runner, which=which)
        self._scheduler_environment = self._build_scheduler_environment(
            repo, runner=runner, which=which
        )

    def validate_scheduler_routes(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> None:
        repo = repo.expanduser().resolve()
        mappings = self._resolve_mappings(repo, runner=runner, which=which)
        routes = self._routes_from_mappings(mappings)
        missing_mapping = [role for role, route in routes.items() if not route]
        if missing_mapping:
            raise role_runtime.RoleRuntimeError(
                "scheduler runtime model route is not registered/discoverable: "
                "AutoDev could not resolve a concrete OpenCode provider/model for "
                + ", ".join(sorted(missing_mapping))
            )

        executable = self._resolve_executable(which=which)
        environment = self._build_process_environment(
            repo, runner=runner, which=which
        )
        try:
            completed = runner(
                [executable, "models"],
                cwd=repo,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime model preflight failed for {self.name}: "
                f"OpenCode model discovery could not be launched: {exc}"
            ) from exc
        returncode = int(getattr(completed, "returncode", 1))
        if returncode != 0:
            stderr = str(getattr(completed, "stderr", "") or "").strip()
            detail = f": {stderr}" if stderr else ""
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime model preflight failed for {self.name}: "
                f"`opencode models` exited with code {returncode}{detail}"
            )
        available = {
            line.strip().split()[0]
            for line in str(getattr(completed, "stdout", "") or "").splitlines()
            if line.strip()
        }
        missing = sorted({route for route in routes.values() if route not in available})
        if missing:
            raise role_runtime.RoleRuntimeError(
                "scheduler runtime model route is not registered/discoverable in OpenCode: "
                + ", ".join(missing)
                + ". AutoDev resolved these provider/model routes, but the selected "
                "runtime cannot instantiate them. Configure the provider/model or "
                "choose another AutoDev model profile, then retry scheduler install/runtime set."
            )

    def scheduler_runtime_environment(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> dict[str, str]:
        if not self._scheduler_environment:
            self._scheduler_environment = self._build_scheduler_environment(
                repo, runner=runner, which=which
            )
        return dict(self._scheduler_environment)

    def scheduler_runtime_routes(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> dict[str, str]:
        mappings = self._resolve_mappings(repo, runner=runner, which=which)
        return self._routes_from_mappings(mappings)

    def scheduler_runtime_identity(
        self,
        repo: Path,
        *,
        runner: Callable[..., object] = subprocess.run,
        which=None,
    ) -> dict[str, object]:
        repo = repo.expanduser().resolve()
        executable = self._resolve_executable(which=which)
        environment = self._build_process_environment(
            repo, runner=runner, which=which
        )
        version = "unknown"
        try:
            completed = runner(
                [executable, "--version"],
                cwd=repo,
                env=environment,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
            if int(getattr(completed, "returncode", 1)) == 0:
                version = str(getattr(completed, "stdout", "") or "").strip() or "unknown"
        except OSError:
            pass

        asset_digest = hashlib.sha256()
        for directory, names in (
            ("agents", opencode_adapter_contract.AGENT_FILES),
            ("commands", opencode_adapter_contract.COMMAND_FILES),
        ):
            for name in sorted(names):
                relative = Path(".opencode") / directory / name
                path = repo / relative
                asset_digest.update(relative.as_posix().encode("utf-8"))
                asset_digest.update(b"\0")
                if path.is_file():
                    asset_digest.update(path.read_bytes())
                asset_digest.update(b"\0")

        overlay = self._generated_provider_overlay(
            self._resolve_mappings(repo, runner=runner, which=which)
        )
        overlay_digest = hashlib.sha256(
            json.dumps(
                overlay,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "runtime": self.name,
            "executable": executable,
            "version": version,
            "asset_sha256": asset_digest.hexdigest(),
            "provider_overlay_sha256": overlay_digest,
        }

    def _resolve_executable(self, *, which=None) -> str:
        if self._scheduler_executable:
            return self._scheduler_executable
        try:
            self._scheduler_executable = opencode_cli.resolve_opencode_cli(which=which)
        except opencode_cli.OpenCodeCliError as exc:
            raise role_runtime.RoleRuntimeError(
                f"scheduler runtime preflight failed for {self.name}: {exc}"
            ) from exc
        return self._scheduler_executable

    def _build_scheduler_environment(
        self,
        repo: Path,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> dict[str, str]:
        executable = self._resolve_executable(which=which)
        path_value = os.environ.get("PATH", "").strip()
        executable_path = Path(executable).expanduser()
        if executable_path.is_absolute():
            directory = str(executable_path.parent.resolve())
            segments = [item for item in path_value.split(os.pathsep) if item]
            if directory not in segments:
                path_value = os.pathsep.join([directory, *segments])
        result: dict[str, str] = {}
        if path_value:
            result["PATH"] = path_value
        mappings = self._resolve_mappings(repo, runner=runner, which=which)
        overlay = self._generated_provider_overlay(mappings)
        if overlay:
            result["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                overlay,
                sort_keys=True,
                separators=(",", ":"),
            )
        return result

    def _build_process_environment(
        self,
        repo: Path,
        *,
        runner: Callable[..., object],
        which=None,
    ) -> dict[str, str]:
        environment = dict(os.environ)
        # Scheduler execution must not depend on an install shell's opaque inline
        # OpenCode config. Only the runtime adapter's non-secret generated overlay
        # is persisted and replayed.
        environment.pop("OPENCODE_CONFIG_CONTENT", None)
        environment.update(
            self._build_scheduler_environment(repo, runner=runner, which=which)
        )
        return environment

    @staticmethod
    def _routes_from_mappings(
        mappings: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        return {
            role: str(value.get("model", "") or "").strip()
            for role, value in mappings.items()
            if role in opencode_adapter_contract.ROLE_NAMES
        }

    @staticmethod
    def _generated_provider_overlay(
        mappings: dict[str, dict[str, str]],
    ) -> dict[str, object]:
        """Generate only provider configuration AutoDev can own safely.

        Credentials remain in OpenCode's user credential store or environment. The
        first managed provider is local Ollama, which otherwise commonly depends on
        an untracked repository-local OpenCode config and disappears in a scheduler
        clone.
        """

        ollama_models: dict[str, object] = {}
        for value in mappings.values():
            route = str(value.get("model", "") or "").strip()
            provider, separator, model_id = route.partition("/")
            if separator and provider == "ollama" and model_id:
                ollama_models[model_id] = {"name": model_id}
        if not ollama_models:
            return {}
        return {
            "provider": {
                "ollama": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Ollama (local)",
                    "options": {"baseURL": "http://localhost:11434/v1"},
                    "models": dict(sorted(ollama_models.items())),
                }
            }
        }

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from area_reader.settings import MARKDOWN_SMOKE_SCRIPT
from automation.workflow_contract import FAILURE_SETUP, WorkflowStageError
from automation.workflow_storage import read_json


BUILTIN_LOCAL_CHECK = "autodev verify-local"
LEGACY_VERIFY_SCRIPT = "codex-verify.ps1"


@dataclass(frozen=True)
class LocalVerificationResult:
    returncode: int
    output: str


def platform_key(platform: str | None = None) -> str:
    value = (platform or sys.platform).casefold()
    if value.startswith("win"):
        return "windows"
    if value.startswith("linux"):
        return "linux"
    if value.startswith("darwin") or value.startswith("mac"):
        return "macos"
    return value or "unknown"


def resolve_template(config: dict[str, object], *, platform: str | None = None) -> str:
    raw = config.get("verifyCommandTemplate", "")
    if isinstance(raw, str):
        template = raw.strip()
    elif isinstance(raw, dict):
        key = platform_key(platform)
        candidates = [key]
        if key == "macos":
            candidates.append("darwin")
        candidates.append("default")
        template = ""
        for candidate in candidates:
            value = raw.get(candidate, "")
            if isinstance(value, str) and value.strip():
                template = value.strip()
                break
    else:
        template = ""
    if not template:
        raise WorkflowStageError(
            "verification profile has no verifyCommandTemplate for the current platform; set LOCAL_CHECK explicitly",
            classification=FAILURE_SETUP,
        )
    return template


def render_profile_command(
    config: dict[str, object],
    *,
    profiles_csv: str,
    autodev_root: Path,
    platform: str | None = None,
    environ: dict[str, str] | None = None,
) -> str:
    template = resolve_template(config, platform=platform)
    env = os.environ if environ is None else environ
    codex_tools = env.get("CODEX_TOOLS_DIR", str(Path.home() / "codex-tools"))
    return (
        template.replace("{~{ProfilesCsv}~}", profiles_csv)
        .replace("{~{AutomationRoot}~}", str(autodev_root))
        .replace("{~{CodexToolsDir}~}", str(Path(codex_tools).expanduser()))
        .strip()
    )


def is_builtin_local_check(command: str) -> bool:
    return " ".join((command or "").split()).casefold() == BUILTIN_LOCAL_CHECK.casefold()


def is_legacy_autodev_default(command: str) -> bool:
    lowered = (command or "").casefold()
    return (
        lowered.lstrip().startswith("pwsh ")
        and LEGACY_VERIFY_SCRIPT in lowered
        and "-profiles" in lowered
    )


def _split_command(command: str, *, platform: str | None = None) -> list[str]:
    try:
        values = shlex.split(command, posix=platform_key(platform) != "windows")
    except ValueError as exc:
        raise WorkflowStageError(
            f"local verification command is malformed: {exc}: {command}",
            classification=FAILURE_SETUP,
        ) from exc
    return [value.strip('"') for value in values if value.strip('"')]


def _is_shipped_profile(profiles_path: Path | None, autodev_root: Path | None) -> bool:
    if profiles_path is None or autodev_root is None:
        return False
    try:
        return profiles_path.expanduser().resolve() == (
            autodev_root.expanduser().resolve() / "codex-profiles.json"
        )
    except OSError:
        return False


def _mixed_platform_path(command: str) -> str:
    for token in re.findall(r'"[^"]+"|\S+', command or ""):
        value = token.strip('"')
        if value.startswith("/") and "\\" in value:
            return value
    return ""


def _referenced_wrapper(tokens: list[str]) -> str:
    for index, token in enumerate(tokens[:-1]):
        if token.casefold() in {"-file", "--file"}:
            return tokens[index + 1]
    for token in tokens[1:]:
        lowered = token.casefold()
        if lowered.endswith((".ps1", ".sh", ".py")) and (
            "/" in token or "\\" in token
        ):
            return token
    return ""


def preflight_local_check(
    command: str,
    *,
    explicit: bool,
    profiles_path: Path | None = None,
    autodev_root: Path | None = None,
    cwd: Path | None = None,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> None:
    command = (command or "").strip()
    if not command:
        raise WorkflowStageError(
            "local verification command is empty",
            classification=FAILURE_SETUP,
        )
    if is_builtin_local_check(command):
        return

    key = platform_key(platform)
    shipped = _is_shipped_profile(profiles_path, autodev_root)
    if key != "windows" and not explicit and shipped and is_legacy_autodev_default(command):
        raise WorkflowStageError(
            "AutoDev's effective default local verification command is Windows-only on "
            f"{key}: {command}; update AutoDev/the shipped verification profile or set LOCAL_CHECK explicitly",
            classification=FAILURE_SETUP,
        )
    if key != "windows" and not explicit and shipped:
        mixed = _mixed_platform_path(command)
        if mixed:
            raise WorkflowStageError(
                "AutoDev generated a mixed-platform local verification path on "
                f"{key}: {mixed}; resolved command: {command}",
                classification=FAILURE_SETUP,
            )

    tokens = _split_command(command, platform=platform)
    if not tokens:
        raise WorkflowStageError(
            "local verification command has no executable",
            classification=FAILURE_SETUP,
        )
    executable = tokens[0]
    if which(executable) is None:
        raise WorkflowStageError(
            f"local verification executable is unavailable: {executable}; resolved command: {command}",
            classification=FAILURE_SETUP,
        )

    # Only stat an interpreted wrapper when validating for the platform we are
    # actually running on. Tests can resolve Windows/Linux templates from either
    # host without pretending Path implements the other platform's filesystem.
    if platform is None or platform_key(platform) == platform_key():
        wrapper = _referenced_wrapper(tokens)
        if wrapper:
            path = Path(wrapper).expanduser()
            if not path.is_absolute() and cwd is not None:
                path = cwd / path
            if not path.is_file():
                raise WorkflowStageError(
                    f"local verification wrapper does not exist: {path}; resolved command: {command}",
                    classification=FAILURE_SETUP,
                )


def _capture(
    runner: Callable[..., object],
    argv: list[str],
    *,
    cwd: Path,
) -> object:
    return runner(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _decoded(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _safe_command_cwd(repo: Path, raw: object) -> Path:
    relative = str(raw or ".").replace("\\", "/").strip() or "."
    candidate = (repo / relative).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError as exc:
        raise WorkflowStageError(
            f"verification command cwd escapes the target repository: {relative}",
            classification=FAILURE_SETUP,
        ) from exc
    if not candidate.is_dir():
        raise WorkflowStageError(
            f"verification command cwd does not exist: {candidate}",
            classification=FAILURE_SETUP,
        )
    return candidate


def _markdown_smoke(repo: Path, runner: Callable[..., object]) -> LocalVerificationResult:
    listing = _capture(runner, ["git", "ls-files", "*.md"], cwd=repo)
    if int(getattr(listing, "returncode", 1)) != 0:
        output = _decoded(getattr(listing, "stdout", "")) + _decoded(
            getattr(listing, "stderr", "")
        )
        raise WorkflowStageError(
            "could not enumerate tracked Markdown files for local verification: "
            + (" ".join(output.split()) or "git ls-files failed"),
            classification=FAILURE_SETUP,
        )
    paths = [line.strip() for line in _decoded(getattr(listing, "stdout", "")).splitlines() if line.strip()]
    if not paths:
        return LocalVerificationResult(0, "No markdown files tracked; skipping markdown smoke check.\n")

    findings: list[str] = []
    for relative in paths:
        path = repo / relative
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise WorkflowStageError(
                f"could not read tracked Markdown file {relative}: {exc}",
                classification=FAILURE_SETUP,
            ) from exc
        for number, line in enumerate(lines, start=1):
            if "\t" in line or line.endswith((" ", "\t")):
                findings.append(f"{relative}:{number}:{line}")
    if findings:
        return LocalVerificationResult(
            1,
            "\n".join(findings)
            + "\nMarkdown smoke check failed: tabs or trailing whitespace found.\n",
        )
    return LocalVerificationResult(0, "Markdown smoke check passed.\n")


def _command_values(raw: object, *, group: str, index: int) -> tuple[list[str], str, bool, str]:
    if not isinstance(raw, dict):
        raise WorkflowStageError(
            f"verification group {group} command {index} is not an object",
            classification=FAILURE_SETUP,
        )
    argv_raw = raw.get("argv", [])
    if not isinstance(argv_raw, list) or not argv_raw or not all(
        isinstance(value, str) and value for value in argv_raw
    ):
        raise WorkflowStageError(
            f"verification group {group} command {index} has invalid argv",
            classification=FAILURE_SETUP,
        )
    return (
        [str(value) for value in argv_raw],
        str(raw.get("cwd", ".") or "."),
        bool(raw.get("optional", False)),
        str(raw.get("label", "") or f"{group} command {index}"),
    )


def run_recommended_verification(
    repo: Path,
    current: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> LocalVerificationResult:
    repo = repo.expanduser().resolve()
    groups_raw = read_json(current / "verification-command-groups.json")
    recommendations = read_json(current / "recommended-command-groups.json")
    if not isinstance(groups_raw, list):
        raise WorkflowStageError(
            "Reader verification-command-groups.json is missing or invalid; rerun Reader before local verification",
            classification=FAILURE_SETUP,
        )
    if not isinstance(recommendations, dict) or not isinstance(
        recommendations.get("recommended_command_groups"), list
    ):
        raise WorkflowStageError(
            "Reader recommended-command-groups.json is missing or invalid; rerun Reader before local verification",
            classification=FAILURE_SETUP,
        )

    groups: dict[str, dict[str, object]] = {}
    for raw in groups_raw:
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            raise WorkflowStageError(
                "verification-command-groups.json contains an invalid group",
                classification=FAILURE_SETUP,
            )
        groups[str(raw["name"])] = raw

    requested = [
        str(value)
        for value in recommendations["recommended_command_groups"]
        if isinstance(value, str) and value
    ]
    output: list[str] = []
    ran = 0
    for name in requested:
        group = groups.get(name)
        if group is None:
            raise WorkflowStageError(
                f"recommended verification group is missing: {name}",
                classification=FAILURE_SETUP,
            )
        if bool(group.get("manual", False)):
            output.append(f"== {name} ==\nmanual reference group; skipped\n")
            continue
        commands = group.get("commands", [])
        if not isinstance(commands, list):
            raise WorkflowStageError(
                f"verification group {name} has invalid commands",
                classification=FAILURE_SETUP,
            )
        output.append(f"== {name} ==\n")
        for index, raw_command in enumerate(commands, start=1):
            argv, raw_cwd, optional, label = _command_values(
                raw_command,
                group=name,
                index=index,
            )
            cwd = _safe_command_cwd(repo, raw_cwd)
            output.append(f"+ ({raw_cwd}) {shlex.join(argv)}\n")
            if argv == ["pwd"]:
                output.append(str(cwd) + "\n")
                ran += 1
                continue
            if argv == ["bash", "-lc", MARKDOWN_SMOKE_SCRIPT]:
                result = _markdown_smoke(repo, runner)
                output.append(result.output)
                ran += 1
                if result.returncode != 0:
                    return LocalVerificationResult(result.returncode, "".join(output))
                continue

            executable = argv[0]
            if which(executable) is None:
                if optional:
                    output.append(f"optional command skipped; executable unavailable: {executable}\n")
                    continue
                raise WorkflowStageError(
                    f"local verification setup is incomplete: required executable {executable!r} is unavailable for {label}",
                    classification=FAILURE_SETUP,
                )
            completed = _capture(runner, argv, cwd=cwd)
            ran += 1
            command_output = _decoded(getattr(completed, "stdout", "")) + _decoded(
                getattr(completed, "stderr", "")
            )
            output.append(command_output)
            returncode = int(getattr(completed, "returncode", 1))
            if returncode != 0:
                if optional:
                    output.append(f"optional command failed with exit code {returncode}; continuing\n")
                    continue
                return LocalVerificationResult(returncode, "".join(output))

    if ran == 0:
        raise WorkflowStageError(
            "recommended verification groups contain no runnable deterministic commands",
            classification=FAILURE_SETUP,
        )
    return LocalVerificationResult(0, "".join(output))


def refreshed_local_check(
    state: dict[str, object],
    autodev_root: Path,
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[str, str, Path]:
    existing = str(state.get("LocalCheck", "")).strip()
    source = str(state.get("LocalCheckSource", "")).strip().casefold()
    explicit_now = os.environ.get("LOCAL_CHECK", "").strip()
    if explicit_now:
        preflight_local_check(
            explicit_now,
            explicit=True,
            cwd=Path(str(state.get("RunDir", "."))).parent.parent,
            platform=platform,
            which=which,
        )
        return explicit_now, "explicit", Path(str(state.get("ProfilesPath", autodev_root / "codex-profiles.json")))
    if source == "explicit":
        return existing, "explicit", Path(str(state.get("ProfilesPath", autodev_root / "codex-profiles.json")))

    profiles_path = Path(
        os.environ.get(
            "PROFILES_PATH",
            str(state.get("ProfilesPath", autodev_root / "codex-profiles.json")),
        )
    ).expanduser()
    should_refresh = source == "profile" or (
        not source
        and is_legacy_autodev_default(existing)
        and _is_shipped_profile(profiles_path, autodev_root)
    )
    if not should_refresh:
        return existing, source or "legacy", profiles_path

    config = read_json(profiles_path)
    if not isinstance(config, dict):
        raise WorkflowStageError(
            f"verification profile configuration is missing or invalid: {profiles_path}",
            classification=FAILURE_SETUP,
        )
    profiles_csv = str(state.get("ProfilesCsv", "auto") or "auto")
    command = render_profile_command(
        config,
        profiles_csv=profiles_csv,
        autodev_root=autodev_root,
        platform=platform,
    )
    preflight_local_check(
        command,
        explicit=False,
        profiles_path=profiles_path,
        autodev_root=autodev_root,
        cwd=Path(str(state.get("RunDir", "."))).parent.parent,
        platform=platform,
        which=which,
    )
    return command, "profile", profiles_path

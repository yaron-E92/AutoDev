from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


INSTALL_SCHEMA = 1
STATE_DIR = Path(".autodev")
STATE_FILE = "install.json"
PROFILE_BEGIN = "# >>> AutoDev PATH >>>"
PROFILE_END = "# <<< AutoDev PATH <<<"


class UserInstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstallResult:
    platform: str
    autodev_root: str
    python: str
    bin_dir: str
    launchers: tuple[str, ...]
    profiles: tuple[str, ...]
    on_path: bool

    def to_json(self) -> dict[str, object]:
        value = asdict(self)
        value["launchers"] = list(self.launchers)
        value["profiles"] = list(self.profiles)
        return value


def _platform_name(value: str | None = None) -> str:
    raw = (value or ("windows" if os.name == "nt" else "posix")).casefold()
    if raw not in {"windows", "posix"}:
        raise UserInstallError(f"unsupported install platform: {raw}")
    return raw


def default_bin_dir(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    local_app_data: str | None = None,
) -> Path:
    platform = _platform_name(platform_name)
    home = (home or Path.home()).expanduser().resolve()
    if platform == "windows":
        base = (local_app_data or os.environ.get("LOCALAPPDATA", "")).strip()
        if base:
            return Path(base).expanduser().resolve() / "AutoDev" / "bin"
        return home / "AppData" / "Local" / "AutoDev" / "bin"
    return home / ".local" / "bin"


def install_state_path(*, home: Path | None = None) -> Path:
    return (home or Path.home()).expanduser().resolve() / STATE_DIR / STATE_FILE


def _posix_launcher(python: str, autodev_root: Path) -> str:
    root = shlex.quote(str(autodev_root))
    executable = shlex.quote(python)
    return (
        "#!/bin/sh\n"
        f"AUTODEV_ROOT={root}\n"
        "if [ -n \"${PYTHONPATH:-}\" ]; then\n"
        "  export PYTHONPATH=\"$AUTODEV_ROOT:$PYTHONPATH\"\n"
        "else\n"
        "  export PYTHONPATH=\"$AUTODEV_ROOT\"\n"
        "fi\n"
        f"exec {executable} -m automation.autodev_cli \"$@\"\n"
    )


def _windows_launcher(python: str, autodev_root: Path) -> str:
    root = str(autodev_root)
    return (
        "@echo off\r\n"
        f"set \"AUTODEV_ROOT={root}\"\r\n"
        "if defined PYTHONPATH (\r\n"
        "  set \"PYTHONPATH=%AUTODEV_ROOT%;%PYTHONPATH%\"\r\n"
        ") else (\r\n"
        "  set \"PYTHONPATH=%AUTODEV_ROOT%\"\r\n"
        ")\r\n"
        f"\"{python}\" -m automation.autodev_cli %*\r\n"
        "exit /b %ERRORLEVEL%\r\n"
    )


def launcher_paths(bin_dir: Path, *, platform_name: str | None = None) -> tuple[Path, ...]:
    platform = _platform_name(platform_name)
    if platform == "windows":
        return (bin_dir / "autodev.cmd",)
    return (bin_dir / "autodev",)


def _profile_block(bin_dir: Path, *, platform_name: str) -> str:
    if platform_name == "windows":
        escaped = str(bin_dir).replace("'", "''")
        line = f"$env:PATH = '{escaped};' + $env:PATH"
    else:
        escaped = str(bin_dir).replace('"', '\\"')
        line = f'export PATH="{escaped}:$PATH"'
    return f"{PROFILE_BEGIN}\n{line}\n{PROFILE_END}"


def _remove_profile_block(text: str) -> str:
    while True:
        start = text.find(PROFILE_BEGIN)
        if start < 0:
            return text
        end = text.find(PROFILE_END, start)
        if end < 0:
            return text
        end += len(PROFILE_END)
        if end < len(text) and text[end] == "\n":
            end += 1
        prefix = text[:start]
        if prefix.endswith("\n") and end < len(text) and text[end:].startswith("\n"):
            end += 1
        text = prefix + text[end:]


def update_profile(profile: Path, bin_dir: Path, *, platform_name: str | None = None) -> None:
    platform = _platform_name(platform_name)
    profile = profile.expanduser().resolve()
    try:
        existing = profile.read_text(encoding="utf-8") if profile.is_file() else ""
    except OSError as exc:
        raise UserInstallError(f"cannot read shell profile {profile}: {exc}") from exc
    clean = _remove_profile_block(existing).rstrip()
    block = _profile_block(bin_dir, platform_name=platform)
    updated = (clean + "\n\n" if clean else "") + block + "\n"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(updated, encoding="utf-8")


def remove_profile_update(profile: Path) -> None:
    profile = profile.expanduser().resolve()
    if not profile.is_file():
        return
    try:
        existing = profile.read_text(encoding="utf-8")
    except OSError as exc:
        raise UserInstallError(f"cannot read shell profile {profile}: {exc}") from exc
    updated = _remove_profile_block(existing)
    if updated != existing:
        profile.write_text(updated, encoding="utf-8")


def _path_contains(bin_dir: Path, *, path_value: str | None = None) -> bool:
    raw = os.environ.get("PATH", "") if path_value is None else path_value
    target = os.path.normcase(os.path.abspath(str(bin_dir)))
    return any(
        os.path.normcase(os.path.abspath(item)) == target
        for item in raw.split(os.pathsep)
        if item.strip()
    )


def install_user(
    autodev_root: Path,
    *,
    python: str = sys.executable,
    bin_dir: Path | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
    profiles: Iterable[Path] = (),
    add_to_path: bool = False,
    path_value: str | None = None,
) -> InstallResult:
    platform = _platform_name(platform_name)
    root = autodev_root.expanduser().resolve()
    if not (root / "automation" / "autodev_cli.py").is_file():
        raise UserInstallError(f"AutoDev root does not contain the canonical CLI: {root}")
    home = (home or Path.home()).expanduser().resolve()
    destination = (bin_dir or default_bin_dir(platform_name=platform, home=home)).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    for launcher in launcher_paths(destination, platform_name=platform):
        content = (
            _windows_launcher(python, root)
            if platform == "windows"
            else _posix_launcher(python, root)
        )
        launcher.write_text(content, encoding="utf-8", newline="")
        if platform == "posix":
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(launcher)

    edited: list[Path] = []
    requested_profiles = [Path(item).expanduser().resolve() for item in profiles]
    if add_to_path and not requested_profiles:
        requested_profiles = [
            home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
            if platform == "windows"
            else home / ".profile"
        ]
    if add_to_path:
        for profile in requested_profiles:
            update_profile(profile, destination, platform_name=platform)
            edited.append(profile)

    result = InstallResult(
        platform=platform,
        autodev_root=str(root),
        python=python,
        bin_dir=str(destination),
        launchers=tuple(str(path) for path in installed),
        profiles=tuple(str(path) for path in edited),
        on_path=_path_contains(destination, path_value=path_value) or bool(edited),
    )
    state_path = install_state_path(home=home)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"schema_version": INSTALL_SCHEMA, **result.to_json()}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def load_install_state(*, home: Path | None = None) -> dict[str, object]:
    path = install_state_path(home=home)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) and value.get("schema_version") == INSTALL_SCHEMA else {}


def uninstall_user(*, home: Path | None = None) -> tuple[str, ...]:
    home = (home or Path.home()).expanduser().resolve()
    state = load_install_state(home=home)
    removed: list[str] = []
    for raw in state.get("launchers", []) if isinstance(state.get("launchers"), list) else []:
        path = Path(str(raw)).expanduser()
        try:
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            raise UserInstallError(f"cannot remove launcher {path}: {exc}") from exc
    for raw in state.get("profiles", []) if isinstance(state.get("profiles"), list) else []:
        remove_profile_update(Path(str(raw)))
    state_path = install_state_path(home=home)
    if state_path.is_file():
        state_path.unlink()
    return tuple(removed)


def run_cli(argv: list[str] | None = None, *, autodev_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autodev install")
    parser.add_argument("--user", action="store_true", help="install the user-local autodev launcher")
    parser.add_argument("--uninstall", action="store_true", help="remove the user-local launcher")
    parser.add_argument("--bin-dir", default="")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--add-to-path", action="store_true")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.user:
        parser.error("--user is required")
    if args.uninstall:
        removed = uninstall_user()
        payload = {"state": "UNINSTALLED", "removed": list(removed)}
        print(json.dumps(payload, sort_keys=True) if args.json else f"Removed {len(removed)} AutoDev launcher(s).")
        return 0
    root = (autodev_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    result = install_user(
        root,
        python=args.python,
        bin_dir=Path(args.bin_dir) if args.bin_dir else None,
        profiles=[Path(value) for value in args.profile],
        add_to_path=bool(args.add_to_path),
    )
    if args.json:
        print(json.dumps({"state": "INSTALLED", **result.to_json()}, sort_keys=True))
    else:
        print(f"Installed AutoDev launcher in {result.bin_dir}.")
        if not result.on_path:
            print(
                f"{result.bin_dir} is not currently on PATH. Re-run with --add-to-path "
                "or add that directory to PATH explicitly."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Callable

from automation import package_release
from automation.product_runtime import BUILD_INFO_FILE


PYINSTALLER_VERSION = "6.16.0"
PAYLOAD_NAME = "autodev"
DATA_ROOTS = (
    "integrations",
    "promptTemplates",
    "agentFiles",
    "examples",
    "docs",
    "README.md",
    "CONTRIBUTING.md",
    "codex-profiles.json",
)


class NativePackagingError(RuntimeError):
    pass


def package_version(version: str) -> str:
    return package_release.validate_version(version).removeprefix("v")


def source_date_epoch(repo: Path, commit: str) -> int:
    raw = package_release.run_git(repo, "show", "-s", "--format=%ct", commit)
    try:
        value = int(raw)
    except ValueError as exc:
        raise NativePackagingError(f"invalid commit timestamp for {commit}: {raw!r}") from exc
    if value <= 0:
        raise NativePackagingError(f"invalid commit timestamp for {commit}: {value}")
    return value


def build_info_text(version: str, commit: str) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "version": package_release.validate_version(version),
            "commit_sha": commit,
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def _data_argument(source: Path, destination: str, *, windows: bool) -> str:
    separator = ";" if windows else ":"
    return f"{source}{separator}{destination}"


def pyinstaller_command(
    repo: Path,
    out_dir: Path,
    work_dir: Path,
    build_info_path: Path,
    *,
    windows: bool,
) -> list[str]:
    entry = repo / "packaging" / "autodev_entry.py"
    command = [
        os.fspath(Path(os.sys.executable)),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--noupx",
        "--name",
        PAYLOAD_NAME,
        "--paths",
        os.fspath(repo),
        "--distpath",
        os.fspath(out_dir),
        "--workpath",
        os.fspath(work_dir / "pyinstaller-work"),
        "--specpath",
        os.fspath(work_dir / "pyinstaller-spec"),
        "--collect-submodules",
        "automation",
        "--collect-submodules",
        "area_reader",
    ]
    for relative in DATA_ROOTS:
        source = repo / relative
        if source.exists():
            destination = "." if source.is_file() else relative
            command.extend(("--add-data", _data_argument(source, destination, windows=windows)))
    command.extend(("--add-data", _data_argument(build_info_path, ".", windows=windows)))
    command.append(os.fspath(entry))
    return command


def normalize_tree_mtimes(root: Path, epoch: int) -> None:
    if not root.exists():
        return
    paths = sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True)
    paths.append(root)
    for path in paths:
        try:
            if path.is_symlink():
                os.utime(path, (epoch, epoch), follow_symlinks=False)
            else:
                os.utime(path, (epoch, epoch))
        except (OSError, NotImplementedError):
            # Some Windows filesystems do not support symlink timestamp updates.
            if not path.is_symlink():
                raise


def build_payload(
    repo: Path,
    out_dir: Path,
    version: str,
    commit: str = "",
    *,
    runner: Callable[..., object] = subprocess.run,
    platform_name: str | None = None,
) -> Path:
    repo = repo.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    version = package_release.validate_version(version)
    commit_sha = package_release.resolve_commit(repo, commit)
    epoch = source_date_epoch(repo, commit_sha)
    platform = (platform_name or ("windows" if os.name == "nt" else "posix")).casefold()
    if platform not in {"windows", "posix"}:
        raise NativePackagingError(f"unsupported native packaging platform: {platform}")
    if not (repo / "packaging" / "autodev_entry.py").is_file():
        raise NativePackagingError("missing packaging/autodev_entry.py")

    work_dir = out_dir / ".native-build"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    build_info_path = work_dir / BUILD_INFO_FILE
    build_info_path.parent.mkdir(parents=True, exist_ok=True)
    build_info_path.write_text(build_info_text(version, commit_sha), encoding="utf-8", newline="\n")

    payload_parent = out_dir / "payload"
    if payload_parent.exists():
        shutil.rmtree(payload_parent)
    payload_parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONHASHSEED"] = "1"
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    command = pyinstaller_command(
        repo,
        payload_parent,
        work_dir,
        build_info_path,
        windows=platform == "windows",
    )
    completed = runner(command, cwd=repo, env=env, check=False)
    returncode = int(getattr(completed, "returncode", 1))
    if returncode != 0:
        stderr = getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or ""
        raise NativePackagingError(f"PyInstaller failed with exit code {returncode}: {stderr}")

    payload = payload_parent / PAYLOAD_NAME
    executable = payload / ("autodev.exe" if platform == "windows" else "autodev")
    if not executable.is_file():
        raise NativePackagingError(f"PyInstaller did not create expected executable: {executable}")
    if platform == "posix":
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    normalize_tree_mtimes(payload, epoch)
    return payload


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the self-contained AutoDev native payload.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--out", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--platform", choices=("windows", "posix"), default=None)
    args = parser.parse_args(argv)
    try:
        payload = build_payload(
            Path(args.repo),
            Path(args.out),
            args.version,
            args.commit,
            platform_name=args.platform,
        )
    except (NativePackagingError, package_release.ReleasePackagingError) as exc:
        parser.error(str(exc))
    print(payload)
    return 0


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())

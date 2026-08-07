from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path


AUTODEV_ROOT = Path(__file__).resolve().parents[1]


def verification_command(
    profiles: str,
    autodev_root: Path = AUTODEV_ROOT,
    *,
    platform: str | None = None,
) -> list[str]:
    autodev_root = autodev_root.expanduser().resolve()
    platform = platform or os.name
    if platform == "nt":
        script = autodev_root / "windows" / "scripts" / "codex-verify.ps1"
        if not script.is_file():
            raise FileNotFoundError(f"Windows verification script is missing: {script}")
        return ["pwsh", "-NoProfile", "-File", str(script), "-Profiles", profiles]

    script = autodev_root / "linux" / "scripts" / "codex-verify.sh"
    if not script.is_file():
        raise FileNotFoundError(f"Linux verification script is missing: {script}")
    return ["bash", str(script), "--profiles", profiles]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dispatch AutoDev verification to the current platform helper.")
    parser.add_argument("--profiles", default="auto")
    parser.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        command = verification_command(args.profiles, Path(args.autodev_root))
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 1
    completed = subprocess.run(command, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

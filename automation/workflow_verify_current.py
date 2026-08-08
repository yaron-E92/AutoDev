from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


AUTODEV_ROOT = Path(__file__).resolve().parents[1]


def current_profiles(repo: Path) -> str:
    state_path = repo / ".codex-run" / "current" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"current AutoDev state is missing or invalid: {state_path}") from exc
    if not isinstance(state, dict):
        raise RuntimeError(f"current AutoDev state is invalid: {state_path}")
    return str(state.get("ProfilesCsv", "")).strip() or "auto"


def run(repo: Path, autodev_root: Path = AUTODEV_ROOT) -> int:
    repo = repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    script = autodev_root / "linux" / "scripts" / "codex-verify.sh"
    if not script.is_file():
        raise RuntimeError(f"Linux verification script is missing: {script}")
    completed = subprocess.run(
        ["bash", str(script), "--profiles", current_profiles(repo)],
        cwd=repo,
        check=False,
    )
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Linux verifier for the current AutoDev profile set.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return run(Path(args.repo), Path(args.autodev_root))
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

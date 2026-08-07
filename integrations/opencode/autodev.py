from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    config_path = root / "autodev.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Invalid AutoDev OpenCode configuration: {config_path}: {exc}", file=sys.stderr)
        return 1

    autodev_root = Path(str(config.get("autodev_root", ""))).expanduser()
    if not autodev_root.is_dir():
        print(f"Configured AutoDev root does not exist: {autodev_root}", file=sys.stderr)
        return 1

    python = str(config.get("python", "")).strip() or sys.executable
    env = dict(os.environ)
    old_python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(autodev_root)
        if not old_python_path
        else str(autodev_root) + os.pathsep + old_python_path
    )
    completed = subprocess.run(
        [python, "-m", "automation.opencode_adapter", *sys.argv[1:]],
        cwd=Path.cwd(),
        env=env,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from automation import local_verification
from automation.workflow_contract import CURRENT_DIR, WorkflowStageError


def run_cli(
    argv: list[str] | None = None,
    *,
    runner=subprocess.run,
    which=shutil.which,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev verify-local")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    current = repo / CURRENT_DIR
    try:
        result = local_verification.run_recommended_verification(
            repo,
            current,
            runner=runner,
            which=which,
        )
    except WorkflowStageError as exc:
        print(f"autodev verify-local: {exc}", file=sys.stderr)
        return 2
    if result.output:
        print(result.output, end="")
    return result.returncode


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())

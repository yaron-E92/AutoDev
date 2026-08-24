from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation.claim_contract import (
    ClaimError,
)
from automation.claim_identity import (
    set_worker_identity,
    worker_identity,
)

def run_worker_cli(
    argv: list[str] | None = None,
    *,
    home: Path | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev scheduler worker-id")
    parser.add_argument("--set", dest="worker_id", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        identity = (
            set_worker_identity(args.worker_id, home=home)
            if args.worker_id
            else worker_identity(home=home)
        )
    except ClaimError as exc:
        print(str(exc), file=stderr)
        return 2
    payload = identity.to_json()
    print(
        json.dumps(payload, sort_keys=True)
        if args.json
        else f"AutoDev worker identity: {identity.worker_id}",
        file=stdout,
    )
    return 0

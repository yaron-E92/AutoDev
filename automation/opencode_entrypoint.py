from __future__ import annotations

import sys

from automation import (
    ci_outcomes,
    opencode_failure_entrypoint,
    opencode_runtime,
    pr_head_sync,
)


COORDINATE_COMMAND = "coordinate"


def run(argv: list[str] | None = None) -> int:
    ci_outcomes.install()
    pr_head_sync.install()
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == COORDINATE_COMMAND:
        return opencode_failure_entrypoint.run(values[1:])
    return opencode_runtime.run(values)


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

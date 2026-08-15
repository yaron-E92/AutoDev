from __future__ import annotations

import sys

from automation import (
    ci_outcomes,
    opencode_github_entrypoint,
    opencode_role_entrypoint,
    opencode_runtime,
    pr_head_sync,
    semantic_repair_budget,
    windows_verification,
)


COORDINATE_COMMAND = "coordinate"
ROLE_COMMAND = "role"


def run(argv: list[str] | None = None) -> int:
    ci_outcomes.install()
    pr_head_sync.install()
    semantic_repair_budget.install_opencode_resume_hooks()
    windows_verification.install_opencode_hooks()
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == COORDINATE_COMMAND:
        return opencode_github_entrypoint.run(values[1:])
    if values and values[0] == ROLE_COMMAND:
        return opencode_role_entrypoint.run(values[1:])
    return opencode_runtime.run(values)


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

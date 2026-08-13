from __future__ import annotations

import os
import subprocess
import sys


WORKFLOW_HINT = (
    " GitHub rejected a workflow-file operation; verify the authenticated token/app "
    "has permission to modify .github/workflows files."
)


def operation_label(arguments: list[str]) -> str:
    if not arguments:
        return "gh"
    if arguments[0] == "api":
        endpoint = arguments[1] if len(arguments) > 1 else "<missing-endpoint>"
        method = "GET"
        for index, token in enumerate(arguments[:-1]):
            if token == "--method":
                method = arguments[index + 1].upper()
                break
        return f"GitHub API {method} {endpoint}"
    if len(arguments) >= 2 and arguments[0] == "pr":
        return f"GitHub PR {arguments[1]}"
    if len(arguments) >= 2 and arguments[0] == "issue":
        return f"GitHub issue {arguments[1]}"
    return "gh " + " ".join(arguments[:2])


def workflow_authorization_hint(stderr: str) -> str:
    lowered = stderr.casefold()
    mentions_workflow = "workflow" in lowered or ".github/workflows" in lowered
    mentions_auth = any(
        marker in lowered
        for marker in ("permission", "scope", "forbidden", "not authorized", "resource not accessible")
    )
    return WORKFLOW_HINT if mentions_workflow and mentions_auth else ""


def main() -> int:
    real_gh = os.environ.get("AUTODEV_REAL_GH", "").strip()
    if not real_gh:
        print("AutoDev gh proxy has no AUTODEV_REAL_GH executable", file=sys.stderr)
        return 2

    arguments = sys.argv[1:]
    input_text = None if sys.stdin.isatty() else sys.stdin.read()
    completed = subprocess.run(
        [real_gh, *arguments],
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env={key: value for key, value in os.environ.items() if key != "AUTODEV_REAL_GH"},
    )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        if completed.returncode != 0:
            sys.stderr.write(f"AutoDev GitHub operation failed: {operation_label(arguments)}: ")
        sys.stderr.write(completed.stderr)
        if completed.returncode != 0:
            hint = workflow_authorization_hint(completed.stderr)
            if hint:
                sys.stderr.write(hint)
                if not hint.endswith("\n"):
                    sys.stderr.write("\n")
    elif completed.returncode != 0:
        sys.stderr.write(
            f"AutoDev GitHub operation failed: {operation_label(arguments)}: no command output\n"
        )
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

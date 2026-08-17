from __future__ import annotations

import argparse
import re
from pathlib import Path


USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s#]+)")
FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class WorkflowValidationError(RuntimeError):
    pass


def workflow_files(repo: Path) -> list[Path]:
    root = repo / ".github" / "workflows"
    return sorted([*root.glob("*.yml"), *root.glob("*.yaml")])


def validate_action_refs(repo: Path) -> list[str]:
    errors: list[str] = []
    files = workflow_files(repo)
    if not files:
        return ["no .github/workflows/*.yml or *.yaml files found"]
    for path in files:
        relative = path.relative_to(repo).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = USES_RE.match(line)
            if not match:
                continue
            value = match.group(1)
            if value.startswith("./") or value.startswith("docker://"):
                continue
            target, separator, ref = value.rpartition("@")
            if not separator or not target or not FULL_SHA_RE.fullmatch(ref):
                errors.append(
                    f"{relative}:{lineno}: external action/workflow must be pinned to a full 40-character commit SHA: {value}"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate immutable external GitHub Action/workflow references."
    )
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    errors = validate_action_refs(Path(args.repo).expanduser().resolve())
    if errors:
        for error in errors:
            print(error)
        return 1
    print("All external workflow action references are pinned to full commit SHAs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

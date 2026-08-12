from __future__ import annotations

import argparse
import os
import shlex
from pathlib import Path

from automation import opencode_adapter


PYTHON_COMMAND_TEMPLATES = (
    "autodev-issue-to-pr.md",
    "autodev-resume.md",
)
PYTHON_SHELL_PLACEHOLDER = "__AUTODEV_PYTHON_SHELL__"


def install_assets(
    target_repo: Path,
    autodev_root: Path = opencode_adapter.AUTODEV_ROOT,
    *,
    python_command: str = "python",
) -> list[Path]:
    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    installed = opencode_adapter.install_assets(
        target_repo,
        autodev_root,
        python_command=python_command,
    )

    source = autodev_root / "integrations" / "opencode" / "python-commands"
    destination = target_repo / ".opencode" / "commands"
    rendered_launcher = shlex.quote(python_command)
    for name in PYTHON_COMMAND_TEMPLATES:
        template_path = source / name
        if not template_path.is_file():
            raise opencode_adapter.OpenCodeAdapterError(
                f"missing canonical Python-coordinator OpenCode command template: {template_path}"
            )
        template = template_path.read_text(encoding="utf-8")
        if template.count(PYTHON_SHELL_PLACEHOLDER) != 1:
            raise opencode_adapter.OpenCodeAdapterError(
                f"Python-coordinator command template must contain exactly one launcher placeholder: {template_path}"
            )
        target = destination / name
        target.write_text(
            template.replace(PYTHON_SHELL_PLACEHOLDER, rendered_launcher),
            encoding="utf-8",
        )
        if target not in installed:
            installed.append(target)
    return installed


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install AutoDev OpenCode assets with deterministic Python coordinator commands."
    )
    parser.add_argument("--target-repo", default=".")
    parser.add_argument("--autodev-root", default=str(opencode_adapter.AUTODEV_ROOT))
    parser.add_argument("--python", default=os.environ.get("PYTHON", "python"))
    args = parser.parse_args(argv)

    installed = install_assets(
        Path(args.target_repo),
        Path(args.autodev_root),
        python_command=args.python,
    )
    print(
        f"Installed {len(installed)} AutoDev OpenCode assets with deterministic Python coordination into "
        f"{Path(args.target_repo).resolve() / '.opencode'}"
    )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

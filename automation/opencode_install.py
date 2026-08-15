from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path

from automation import opencode_adapter


PYTHON_COMMAND_TEMPLATES = (
    "autodev-issue-to-pr.md",
    "autodev-resume.md",
    "autodev-read.md",
    "autodev-plan.md",
    "autodev-implement.md",
    "autodev-fix.md",
    "autodev-verify.md",
)
PYTHON_SHELL_PLACEHOLDER = "__AUTODEV_PYTHON_SHELL__"
WINDOWS_WORKFLOW_REF_PLACEHOLDER = "__AUTODEV_WORKFLOW_REF__"
WINDOWS_CALLER_TEMPLATE = Path("integrations") / "github-actions" / "autodev-windows-verification.yml"
WINDOWS_CALLER_TARGET = Path(".github") / "workflows" / "autodev-windows-verification.yml"


def _autodev_workflow_ref(autodev_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(autodev_root), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        return "main"
    value = str(completed.stdout or "").strip()
    return value if completed.returncode == 0 and len(value) == 40 else "main"


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

    workflow_template = autodev_root / WINDOWS_CALLER_TEMPLATE
    if not workflow_template.is_file():
        raise opencode_adapter.OpenCodeAdapterError(
            f"missing canonical Windows verification caller workflow: {workflow_template}"
        )
    workflow_text = workflow_template.read_text(encoding="utf-8")
    if workflow_text.count(WINDOWS_WORKFLOW_REF_PLACEHOLDER) != 2:
        raise opencode_adapter.OpenCodeAdapterError(
            "Windows verification caller workflow must contain exactly two AutoDev ref placeholders"
        )
    workflow_target = target_repo / WINDOWS_CALLER_TARGET
    workflow_target.parent.mkdir(parents=True, exist_ok=True)
    workflow_target.write_text(
        workflow_text.replace(
            WINDOWS_WORKFLOW_REF_PLACEHOLDER,
            _autodev_workflow_ref(autodev_root),
        ),
        encoding="utf-8",
    )
    if workflow_target not in installed:
        installed.append(workflow_target)
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
    target = Path(args.target_repo).resolve()
    print(
        f"Installed {len(installed)} AutoDev assets into {target}. "
        f"Commit {WINDOWS_CALLER_TARGET.as_posix()} to the target repository default branch "
        "before a Windows-required AutoDev run can dispatch GitHub Actions verification."
    )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

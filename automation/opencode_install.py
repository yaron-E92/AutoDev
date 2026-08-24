from __future__ import annotations

from automation import windows_verification_contract

from automation import windows_verification_config

from automation import opencode_adapter_contract

from automation import opencode_adapter_assets

import argparse
import json
import os
from pathlib import Path



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
LEGACY_COMMAND_PREFIX = f"{PYTHON_SHELL_PLACEHOLDER} .opencode/autodev.py"
WINDOWS_CALLER_TEMPLATE = Path("integrations") / "github-actions" / "autodev-windows-verification.yml"
WINDOWS_CALLER_TARGET = Path(".github") / "workflows" / "autodev-windows-verification.yml"
WINDOWS_SETUP_PLACEHOLDER = "      # __AUTODEV_REPOSITORY_SETUP__"
LEGACY_BRIDGE_CONFIG = Path(".opencode") / "autodev.json"


def _render_windows_setup(config: dict[str, object] | None) -> str:
    setup = config.get("setup") if config else None
    if not isinstance(setup, dict):
        return ""
    name = json.dumps(str(setup["name"]))
    command_lines = str(setup["command"]).splitlines()
    secret_env = setup.get("secret_env", {})
    lines = [
        f"      - name: {name}",
        "        shell: pwsh",
        "        working-directory: target",
    ]
    if isinstance(secret_env, dict) and secret_env:
        lines.append("        env:")
        for environment_name, secret_name in sorted(secret_env.items()):
            lines.append(f"          {environment_name}: ${{{{ secrets.{secret_name} }}}}")
    lines.append("        run: |")
    if isinstance(secret_env, dict):
        for environment_name, secret_name in sorted(secret_env.items()):
            lines.extend(
                [
                    f"          if ([string]::IsNullOrWhiteSpace($env:{environment_name})) {{",
                    f"            throw \"Required Actions secret {secret_name} is unavailable for repository setup.\"",
                    "          }",
                ]
            )
    lines.extend(f"          {line}" for line in command_lines)
    return "\n".join(lines)


def _remove_legacy_bridge_config(target_repo: Path, installed: list[Path]) -> None:
    path = target_repo / LEGACY_BRIDGE_CONFIG
    if not path.is_file():
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise opencode_adapter_contract.OpenCodeAdapterError(
            f"legacy AutoDev OpenCode config is not recognized and will not be removed: {path}"
        ) from exc
    if not isinstance(value, dict) or value.get("version") != 1 or not set(value).issubset(
        {"version", "autodev_root", "python"}
    ):
        raise opencode_adapter_contract.OpenCodeAdapterError(
            f"legacy AutoDev OpenCode config is not recognized and will not be removed: {path}"
        )
    path.unlink()
    installed[:] = [item for item in installed if item != path]


def _modernize_agent_text(text: str) -> str:
    """Render an installed OpenCode agent against the canonical global CLI.

    The checked-in integration templates remain usable by the low-level legacy
    installer during the migration window. The canonical installer removes the
    machine-specific .opencode/autodev.json dependency and makes `autodev` the
    only launcher an installed agent is expected to invoke.
    """

    replacements = (
        (
            "read `.opencode/autodev.json` once and use its non-empty `python` field as the exact bridge launcher",
            "use the installed `autodev` command as the exact bridge launcher",
        ),
        (
            "Read `.opencode/autodev.json` once and use its non-empty `python` field as the exact bridge launcher",
            "Use the installed `autodev` command as the exact bridge launcher",
        ),
        (
            "use the installer-selected launcher from `.opencode/autodev.json`",
            "use the installed `autodev` command",
        ),
        (
            "Use the installer-selected launcher from `.opencode/autodev.json`",
            "Use the installed `autodev` command",
        ),
        (
            "use its non-empty `python` field as the exact bridge launcher",
            "use the installed `autodev` command as the exact bridge launcher",
        ),
        (
            "Never edit `.opencode/autodev.json`; it is installer-owned bridge configuration.",
            "Never rewrite user-owned OpenCode configuration merely to choose the AutoDev launcher.",
        ),
    )
    for old, new in replacements:
        text = text.replace(old, new)

    rendered: list[str] = []
    seen_permission_lines: set[str] = set()
    in_frontmatter = True
    frontmatter_delimiters = 0
    for line in text.splitlines():
        if line.strip() == "---":
            frontmatter_delimiters += 1
            if frontmatter_delimiters >= 2:
                in_frontmatter = False
        if '".opencode/autodev.json": allow' in line:
            continue
        if in_frontmatter and '"python3 .opencode/autodev.py ' in line:
            continue
        if in_frontmatter and '"python .opencode/autodev.py ' in line:
            line = line.replace("python .opencode/autodev.py", "autodev")
            if line in seen_permission_lines:
                continue
            seen_permission_lines.add(line)
        rendered.append(line)
    text = "\n".join(rendered) + "\n"

    text = text.replace("python3 .opencode/autodev.py", "autodev")
    text = text.replace("python .opencode/autodev.py", "autodev")
    text = text.replace("`.opencode/autodev.json`", "the installed `autodev` launcher")

    instruction = (
        "\n**Canonical AutoDev launcher:** use the installed `autodev` command exactly; "
        "do not probe for Python interpreters or alternate bridge paths. If a generated "
        "legacy role-contract command begins with `python .opencode/autodev.py` or "
        "`python3 .opencode/autodev.py`, replace only that leading compatibility prefix "
        "with `autodev` and preserve every remaining argument. Repository-local "
        "`.opencode/autodev.py` / `.opencode/autodev.ps1` are temporary compatibility "
        "shims, not configuration sources.\n"
    )
    text += instruction
    return text


def _modernize_installed_agents(target_repo: Path) -> None:
    agents = target_repo / ".opencode" / "agents"
    for name in opencode_adapter_contract.AGENT_FILES:
        path = agents / name
        if not path.is_file():
            raise opencode_adapter_contract.OpenCodeAdapterError(
                f"installed OpenCode agent is missing: {path}"
            )
        path.write_text(
            _modernize_agent_text(path.read_text(encoding="utf-8")),
            encoding="utf-8",
        )


def _render_python_command(template: str, template_path: Path) -> str:
    if template.count(PYTHON_SHELL_PLACEHOLDER) != 1 or template.count(LEGACY_COMMAND_PREFIX) != 1:
        raise opencode_adapter_contract.OpenCodeAdapterError(
            "Python-coordinator command template must contain exactly one canonical legacy bridge prefix: "
            f"{template_path}"
        )
    return template.replace(LEGACY_COMMAND_PREFIX, "autodev")


def install_assets(
    target_repo: Path,
    autodev_root: Path = opencode_adapter_contract.AUTODEV_ROOT,
    *,
    python_command: str = "python",
) -> list[Path]:
    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    installed = opencode_adapter_assets.install_assets(
        target_repo,
        autodev_root,
        python_command=python_command,
    )

    # The low-level legacy adapter still emits .opencode/autodev.json for
    # backward-compatible direct callers. The canonical installer removes it:
    # generic AutoDev configuration must not live under the OpenCode namespace.
    _remove_legacy_bridge_config(target_repo, installed)
    _modernize_installed_agents(target_repo)

    source = autodev_root / "integrations" / "opencode" / "python-commands"
    destination = target_repo / ".opencode" / "commands"
    for name in PYTHON_COMMAND_TEMPLATES:
        template_path = source / name
        if not template_path.is_file():
            raise opencode_adapter_contract.OpenCodeAdapterError(
                f"missing canonical Python-coordinator OpenCode command template: {template_path}"
            )
        target = destination / name
        target.write_text(
            _render_python_command(template_path.read_text(encoding="utf-8"), template_path),
            encoding="utf-8",
        )
        if target not in installed:
            installed.append(target)

    workflow_template = autodev_root / WINDOWS_CALLER_TEMPLATE
    if not workflow_template.is_file():
        raise opencode_adapter_contract.OpenCodeAdapterError(
            f"missing canonical Windows verification caller workflow: {workflow_template}"
        )
    workflow_text = workflow_template.read_text(encoding="utf-8")
    if workflow_text.count(WINDOWS_SETUP_PLACEHOLDER) != 1:
        raise opencode_adapter_contract.OpenCodeAdapterError(
            f"Windows verification caller template must contain exactly one setup placeholder: {workflow_template}"
        )
    try:
        windows_config = windows_verification_config.load_config(target_repo)
    except windows_verification_contract.WindowsVerificationError as exc:
        raise opencode_adapter_contract.OpenCodeAdapterError(str(exc)) from exc
    workflow_target = target_repo / WINDOWS_CALLER_TARGET
    workflow_target.parent.mkdir(parents=True, exist_ok=True)
    workflow_target.write_text(
        workflow_text.replace(WINDOWS_SETUP_PLACEHOLDER, _render_windows_setup(windows_config)),
        encoding="utf-8",
    )
    if workflow_target not in installed:
        installed.append(workflow_target)
    return installed


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install or update the complete AutoDev OpenCode integration."
    )
    parser.add_argument("--target-repo", default=".")
    parser.add_argument("--autodev-root", default=str(opencode_adapter_contract.AUTODEV_ROOT))
    parser.add_argument("--python", default=os.environ.get("PYTHON", "python"))
    args = parser.parse_args(argv)

    installed = install_assets(
        Path(args.target_repo),
        Path(args.autodev_root),
        python_command=args.python,
    )
    target = Path(args.target_repo).resolve()
    print(
        f"Installed {len(installed)} AutoDev OpenCode assets into {target}. "
        "OpenCode commands invoke the first-class `autodev` CLI; repository-local wrappers remain compatibility shims only. "
        f"If {WINDOWS_CALLER_TARGET.as_posix()} is new or changed, commit/merge it to the target "
        "repository default branch before a Windows-required AutoDev run can dispatch GitHub Actions verification."
    )
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

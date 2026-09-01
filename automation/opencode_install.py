from __future__ import annotations

import json
from pathlib import Path

from automation import opencode_adapter_assets
from automation import opencode_adapter_contract
from automation import windows_verification_config
from automation import windows_verification_contract

WINDOWS_CALLER_TEMPLATE = Path("integrations") / "github-actions" / "autodev-windows-verification.yml"
WINDOWS_CALLER_TARGET = Path(".github") / "workflows" / "autodev-windows-verification.yml"
WINDOWS_SETUP_PLACEHOLDER = "      # __AUTODEV_REPOSITORY_SETUP__"


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


def install_assets(
    target_repo: Path,
    autodev_root: Path = opencode_adapter_contract.AUTODEV_ROOT,
) -> list[Path]:
    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    installed = opencode_adapter_assets.install_assets(target_repo, autodev_root)

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
    rendered_setup = _render_windows_setup(windows_config)
    if rendered_setup:
        rendered_workflow = workflow_text.replace(WINDOWS_SETUP_PLACEHOLDER, rendered_setup)
    else:
        rendered_workflow = workflow_text.replace(f"{WINDOWS_SETUP_PLACEHOLDER}\n\n", "")
    workflow_target.write_text(rendered_workflow, encoding="utf-8")
    if workflow_target not in installed:
        installed.append(workflow_target)
    return installed

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OLD_WINDOWS = (
    "windows/scripts/codex-common.ps1",
    "windows/scripts/codex-finalize-current-issue.ps1",
    "windows/scripts/codex-mark-current-issue.ps1",
    "windows/scripts/codex-plan-current-issue.ps1",
    "windows/scripts/codex-prepare-next-ready-issue.ps1",
    "windows/scripts/ensure-codex-labels.ps1",
    "windows/scripts/issue-to-pr-cycle.ps1",
)
OLD_TESTS = (
    "tests/test_linux_codex_verify.py",
    "tests/test_workflow_stage_wrappers.py",
    "tests/test_opencode_bridge.py",
    "tests/test_opencode_legacy_snapshot_compat.py",
)


def remove(relative: str) -> None:
    path = ROOT / relative
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def canonicalize_agent(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
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
        (
            "using the configured launcher",
            "using the installed `autodev` launcher",
        ),
        (
            "using the same configured launcher",
            "using the same `autodev` launcher",
        ),
    )
    for old, new in replacements:
        text = text.replace(old, new)

    rendered: list[str] = []
    seen_permissions: set[str] = set()
    in_frontmatter = True
    delimiters = 0
    for line in text.splitlines():
        if line.strip() == "---":
            delimiters += 1
            if delimiters >= 2:
                in_frontmatter = False
        if '".opencode/autodev.json": allow' in line:
            continue
        if in_frontmatter and ('"python3 .opencode/autodev.py ' in line or '"python .opencode/autodev.py ' in line):
            line = line.replace("python3 .opencode/autodev.py", "autodev")
            line = line.replace("python .opencode/autodev.py", "autodev")
            if line in seen_permissions:
                continue
            seen_permissions.add(line)
        rendered.append(line)
    text = "\n".join(rendered) + "\n"
    text = text.replace("python3 .opencode/autodev.py", "autodev")
    text = text.replace("python .opencode/autodev.py", "autodev")
    text = text.replace("`.opencode/autodev.json`", "the installed `autodev` launcher")
    text = text.replace(
        "In generated role-contract commands, replace only the leading canonical `python` token with that configured launcher when necessary; preserve the rest of the command exactly.",
        "Role-contract commands already use `autodev`; preserve every argument exactly.",
    )
    if "**Canonical AutoDev launcher:**" not in text:
        text += (
            "\n**Canonical AutoDev launcher:** use the installed `autodev` command exactly; "
            "do not probe for Python interpreters or repository-local bridge paths. "
            "Role-contract commands already use `autodev`; preserve every remaining argument.\n"
        )
    path.write_text(text, encoding="utf-8")


def canonicalize_opencode_assets() -> None:
    root = ROOT / "integrations" / "opencode"
    old_commands = root / "commands"
    new_commands = root / "python-commands"
    status = (old_commands / "autodev-status.md").read_text(encoding="utf-8")
    shutil.rmtree(old_commands)
    old_commands.mkdir(parents=True)
    for source in sorted(new_commands.iterdir()):
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        text = text.replace("__AUTODEV_PYTHON_SHELL__ .opencode/autodev.py", "autodev")
        (old_commands / source.name).write_text(text, encoding="utf-8")
    status = status.replace("Run the installed bridge (use `python3` instead where required):", "Run the installed AutoDev CLI:")
    status = status.replace("python .opencode/autodev.py status", "autodev status")
    status = status.replace("bridge status", "AutoDev status")
    (old_commands / "autodev-status.md").write_text(status, encoding="utf-8")
    shutil.rmtree(new_commands)
    remove("integrations/opencode/autodev.py")
    remove("integrations/opencode/autodev.ps1")
    for agent in (root / "agents").glob("*.md"):
        canonicalize_agent(agent)


def rewrite_contract() -> None:
    path = ROOT / "automation" / "opencode_adapter_contract.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("python .opencode/autodev.py", "autodev")
    path.write_text(text, encoding="utf-8")


def rewrite_assets_module() -> None:
    path = ROOT / "automation" / "opencode_adapter_assets.py"
    path.write_text(
        '''from __future__ import annotations\n\nimport shutil\nfrom pathlib import Path\n\nfrom automation.opencode_adapter_contract import (\n    AGENT_FILES,\n    AUTODEV_ROOT,\n    COMMAND_FILES,\n    OpenCodeAdapterError,\n)\n\n\ndef install_assets(\n    target_repo: Path,\n    autodev_root: Path = AUTODEV_ROOT,\n) -> list[Path]:\n    """Install canonical OpenCode commands and agents that invoke `autodev`."""\n    target_repo = target_repo.expanduser().resolve()\n    autodev_root = autodev_root.expanduser().resolve()\n    if not target_repo.is_dir():\n        raise OpenCodeAdapterError(f"target repository is not a directory: {target_repo}")\n\n    source = autodev_root / "integrations" / "opencode"\n    target = target_repo / ".opencode"\n    installed: list[Path] = []\n    for directory, names in (("commands", COMMAND_FILES), ("agents", AGENT_FILES)):\n        destination = target / directory\n        destination.mkdir(parents=True, exist_ok=True)\n        for name in names:\n            source_file = source / directory / name\n            if not source_file.is_file():\n                raise OpenCodeAdapterError(f"missing canonical OpenCode asset: {source_file}")\n            target_file = destination / name\n            shutil.copyfile(source_file, target_file)\n            installed.append(target_file)\n    return installed\n''',
        encoding="utf-8",
    )


def rewrite_install_module() -> None:
    path = ROOT / "automation" / "opencode_install.py"
    path.write_text(
        '''from __future__ import annotations\n\nimport json\nfrom pathlib import Path\n\nfrom automation import opencode_adapter_assets\nfrom automation import opencode_adapter_contract\nfrom automation import windows_verification_config\nfrom automation import windows_verification_contract\n\nWINDOWS_CALLER_TEMPLATE = Path("integrations") / "github-actions" / "autodev-windows-verification.yml"\nWINDOWS_CALLER_TARGET = Path(".github") / "workflows" / "autodev-windows-verification.yml"\nWINDOWS_SETUP_PLACEHOLDER = "      # __AUTODEV_REPOSITORY_SETUP__"\n\n\ndef _render_windows_setup(config: dict[str, object] | None) -> str:\n    setup = config.get("setup") if config else None\n    if not isinstance(setup, dict):\n        return ""\n    name = json.dumps(str(setup["name"]))\n    command_lines = str(setup["command"]).splitlines()\n    secret_env = setup.get("secret_env", {})\n    lines = [\n        f"      - name: {name}",\n        "        shell: pwsh",\n        "        working-directory: target",\n    ]\n    if isinstance(secret_env, dict) and secret_env:\n        lines.append("        env:")\n        for environment_name, secret_name in sorted(secret_env.items()):\n            lines.append(f"          {environment_name}: ${{{{ secrets.{secret_name} }}}}")\n    lines.append("        run: |")\n    if isinstance(secret_env, dict):\n        for environment_name, secret_name in sorted(secret_env.items()):\n            lines.extend(\n                [\n                    f"          if ([string]::IsNullOrWhiteSpace($env:{environment_name})) {{",\n                    f"            throw \\\"Required Actions secret {secret_name} is unavailable for repository setup.\\\"",\n                    "          }",\n                ]\n            )\n    lines.extend(f"          {line}" for line in command_lines)\n    return "\\n".join(lines)\n\n\ndef install_assets(\n    target_repo: Path,\n    autodev_root: Path = opencode_adapter_contract.AUTODEV_ROOT,\n) -> list[Path]:\n    target_repo = target_repo.expanduser().resolve()\n    autodev_root = autodev_root.expanduser().resolve()\n    installed = opencode_adapter_assets.install_assets(target_repo, autodev_root)\n\n    workflow_template = autodev_root / WINDOWS_CALLER_TEMPLATE\n    if not workflow_template.is_file():\n        raise opencode_adapter_contract.OpenCodeAdapterError(\n            f"missing canonical Windows verification caller workflow: {workflow_template}"\n        )\n    workflow_text = workflow_template.read_text(encoding="utf-8")\n    if workflow_text.count(WINDOWS_SETUP_PLACEHOLDER) != 1:\n        raise opencode_adapter_contract.OpenCodeAdapterError(\n            f"Windows verification caller template must contain exactly one setup placeholder: {workflow_template}"\n        )\n    try:\n        windows_config = windows_verification_config.load_config(target_repo)\n    except windows_verification_contract.WindowsVerificationError as exc:\n        raise opencode_adapter_contract.OpenCodeAdapterError(str(exc)) from exc\n    workflow_target = target_repo / WINDOWS_CALLER_TARGET\n    workflow_target.parent.mkdir(parents=True, exist_ok=True)\n    workflow_target.write_text(\n        workflow_text.replace(WINDOWS_SETUP_PLACEHOLDER, _render_windows_setup(windows_config)),\n        encoding="utf-8",\n    )\n    if workflow_target not in installed:\n        installed.append(workflow_target)\n    return installed\n''',
        encoding="utf-8",
    )


def rewrite_adapter_cli() -> None:
    path = ROOT / "automation" / "opencode_adapter_cli.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import os\n", "")
    text = re.sub(
        r'\n    install = subparsers\.add_parser\("install"\)\n'
        r'    install\.add_argument\("--target-repo", default="\."\)\n'
        r'    install\.add_argument\("--autodev-root", default=str\(AUTODEV_ROOT\)\)\n'
        r'    install\.add_argument\("--python", default=os\.environ\.get\("PYTHON", "python"\)\)\n',
        "\n",
        text,
    )
    text = re.sub(
        r'        if args\.command == "install":\n(?:            .*\n)+?            \)\n',
        "",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def rewrite_repo_setup() -> None:
    path = ROOT / "automation" / "repo_setup.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('LEGACY_OPENCODE_CONFIG = Path(".opencode") / "autodev.json"\n', "")
    text = text.replace("    removed_legacy: tuple[str, ...]\n", "")
    text = text.replace('(\"created\", \"updated\", \"removed_legacy\", \"labels_created\")', '("created", "updated", "labels_created")')
    text = re.sub(
        r'\ndef _legacy_config_is_autodev_owned\(path: Path\) -> bool:\n.*?\n\ndef _validate_repo_policy',
        "\n\ndef _validate_repo_policy",
        text,
        flags=re.S,
    )
    text = text.replace("    python_command: str = sys.executable,\n", "")
    text = text.replace("    removed: list[str] = []\n", "")
    text = re.sub(
        r'\n    legacy = repo / LEGACY_OPENCODE_CONFIG\n'
        r'    legacy_before = .*?\n'
        r'    if enable_opencode:\n'
        r'(.*?)'
        r'    elif legacy\.is_file\(\):\n'
        r'        # .*?\n'
        r'        pass\n',
        lambda match: "\n    if enable_opencode:\n" + match.group(1),
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace("            python_command=python_command,\n", "")
    text = re.sub(
        r'        if legacy_before and not legacy\.exists\(\):\n'
        r'            removed\.append\(LEGACY_OPENCODE_CONFIG\.as_posix\(\)\)\n',
        "",
        text,
    )
    text = text.replace("        removed_legacy=tuple(sorted(set(removed))),\n", "")
    text = re.sub(
        r'\n    legacy = repo / LEGACY_OPENCODE_CONFIG\n'
        r'    checks\.append\(\n'
        r'        DoctorCheck\(\n'
        r'            "legacy-opencode-config",.*?\n'
        r'        \)\n'
        r'    \)\n',
        "\n",
        text,
        count=1,
        flags=re.S,
    )
    text = text.replace('    install_parser.add_argument("--python", default=sys.executable)\n', "")
    text = text.replace('    doctor_parser.add_argument("--python", default=sys.executable)\n', "")
    text = text.replace("                python_command=args.python,\n", "")
    path.write_text(text, encoding="utf-8")


def rewrite_windows_readme() -> None:
    path = ROOT / "windows" / "scripts" / "README.md"
    path.write_text(
        "# Windows helper scripts\n\n"
        "These scripts are implementation helpers for canonical AutoDev Windows verification. "
        "They are not alternative issue-to-PR entrypoints.\n\n"
        "- `windows-verification-worker.ps1` executes the exact-source Windows verification request used by GitHub Actions.\n"
        "- `configure-nuget-source.ps1` configures an optional authenticated package source for repository-specific Windows verification setup.\n\n"
        "Normal AutoDev operation starts through the installed `autodev` CLI.\n",
        encoding="utf-8",
    )


def update_architecture_guard() -> None:
    path = ROOT / "tests" / "test_python_architecture.py"
    text = path.read_text(encoding="utf-8")
    marker = '    "automation/ollama_cloud_preflight.py",\n'
    additions = (
        marker
        + '    "automation/workflow_stage_legacy.py",\n'
        + '    "automation/workflow_verify_current.py",\n'
        + '    "integrations/opencode/autodev.py",\n'
        + '    "integrations/opencode/autodev.ps1",\n'
        + '    "integrations/opencode/python-commands",\n'
        + '    "linux",\n'
        + '    "windows/scripts/issue-to-pr-cycle.ps1",\n'
        + '    "windows/scripts/codex-common.ps1",\n'
        + '    "windows/scripts/codex-finalize-current-issue.ps1",\n'
        + '    "windows/scripts/codex-mark-current-issue.ps1",\n'
        + '    "windows/scripts/codex-plan-current-issue.ps1",\n'
        + '    "windows/scripts/codex-prepare-next-ready-issue.ps1",\n'
        + '    "windows/scripts/ensure-codex-labels.ps1",\n'
    )
    if marker not in text:
        raise SystemExit("architecture guard insertion marker not found")
    path.write_text(text.replace(marker, additions, 1), encoding="utf-8")


def update_docs_basics() -> None:
    replacements = {
        "docs/opencode.md": (
            ("python -m automation.opencode_install --target-repo <TARGET_REPOSITORY>", "autodev repo install --repo <TARGET_REPOSITORY>"),
            ("python -m automation.opencode_adapter install", "autodev repo install"),
            ("python -m automation.opencode_install `\n  --target-repo C:\\source\\repos\\TARGET_REPOSITORY", "autodev repo install --repo C:\\source\\repos\\TARGET_REPOSITORY"),
            ("python3 -m automation.opencode_install \\\n  --target-repo ~/src/TARGET_REPOSITORY \\\n  --python python3", "autodev repo install --repo ~/src/TARGET_REPOSITORY"),
            ("The bridge reads `.opencode/autodev.json`, adds the configured AutoDev checkout to `PYTHONPATH`, and invokes `automation.opencode_adapter` with the configured Python command.", "Installed OpenCode commands invoke the user-level `autodev` CLI directly; repository-local Python bridge configuration is not part of the canonical runtime."),
        ),
    }
    for relative, pairs in replacements.items():
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        for old, new in pairs:
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def main() -> int:
    remove("linux")
    for relative in OLD_WINDOWS:
        remove(relative)
    for relative in OLD_TESTS:
        remove(relative)
    remove("automation/workflow_stage_legacy.py")
    remove("automation/workflow_verify_current.py")
    canonicalize_opencode_assets()
    rewrite_contract()
    rewrite_assets_module()
    rewrite_install_module()
    rewrite_adapter_cli()
    rewrite_repo_setup()
    rewrite_windows_readme()
    update_architecture_guard()
    update_docs_basics()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

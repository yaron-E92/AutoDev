from __future__ import annotations

import os
import shlex
from area_reader_v2.command_group_recommendations import recommend_command_groups as recommend_area_reader_command_groups

from area_reader_v2.area_reader_settings import (
    MARKDOWN_SMOKE_SCRIPT,
    PREFERRED_SOLUTION_FILTER_MARKERS,
)

def command(label, cwd, argv, optional=False):
    return {
        "label": label,
        "cwd": cwd,
        "argv": argv,
        "optional": optional,
    }

def command_group(name, description, commands, recommended=False, reason="", manual=False):
    return {
        "name": name,
        "description": description,
        "recommended": recommended,
        "reason": reason,
        "manual": manual,
        "commands": commands,
    }

def script_command_for_package(package_info, script_name):
    manager = package_info["package_manager"]
    if manager == "yarn":
        return ["yarn", script_name]
    if manager == "bun":
        return ["bun", "run", script_name]
    return [manager, "run", script_name]

def preferred_solution_filter(solution_filters):
    for solution_filter in solution_filters:
        normalized = solution_filter.casefold()
        if any(marker in normalized for marker in PREFERRED_SOLUTION_FILTER_MARKERS):
            return solution_filter
    return None

def dotnet_solution_targets(facts):
    solution_filters = facts.get("solution_filters", [])
    preferred_filter = preferred_solution_filter(solution_filters)
    if preferred_filter:
        return [preferred_filter]
    solutions = facts.get("solutions", [])
    if solutions:
        return list(solutions)
    return list(solution_filters)

def build_verification_command_groups(facts, areas):
    area_set = set(areas)
    groups = []

    groups.append(
        command_group(
            "env",
            "Print local tool versions useful for interpreting benchmark verification.",
            [
                command("Show working directory", ".", ["pwd"]),
                command("Show Python version", ".", ["python3", "--version"], optional=True),
                command("Show dotnet SDK info", ".", ["dotnet", "--info"], optional=True),
                command("Show Node version", ".", ["node", "--version"], optional=True),
                command("Show npm version", ".", ["npm", "--version"], optional=True),
            ],
            recommended=True,
            reason="Always useful for local environment diagnostics.",
        )
    )

    dotnet_commands = []
    dotnet_targets = dotnet_solution_targets(facts)
    preferred_filter = preferred_solution_filter(facts.get("solution_filters", []))
    for solution in dotnet_targets:
        dotnet_commands.append(command(f"Restore {solution}", ".", ["dotnet", "restore", solution]))
        dotnet_commands.append(
            command(
                f"Build {solution}",
                ".",
                ["dotnet", "build", solution, "--no-restore", "--verbosity", "minimal"],
            )
        )
        dotnet_commands.append(
            command(
                f"Test {solution}",
                ".",
                ["dotnet", "test", solution, "--no-build", "--verbosity", "minimal"],
            )
        )
    if preferred_filter:
        dotnet_reason = f"Detected preferred .NET solution filter: {preferred_filter}."
    elif dotnet_commands:
        dotnet_reason = "Detected .NET solution files or filters."
    else:
        dotnet_reason = "No .NET solution files or filters detected."
    groups.append(
        command_group(
            "dotnet-solution",
            "Restore, build, and test the preferred .NET solution/filter verification surface from the repository root.",
            dotnet_commands,
            recommended=bool(dotnet_commands and area_set & {"backend", "maui", "tests"}),
            reason=dotnet_reason,
        )
    )

    node_commands = []
    for package_info in facts["package_roots"]:
        install = package_info["install_command"]
        node_commands.append(
            command(
                f"Install dependencies in {package_info['root']}",
                package_info["root"],
                install,
                optional=install == ["npm", "install"],
            )
        )
    groups.append(
        command_group(
            "node-root",
            "Install dependencies for detected JavaScript package roots.",
            node_commands,
            recommended=bool(node_commands and area_set & {"web", "api-client", "tests"}),
            reason="Detected package.json files." if node_commands else "No package.json files detected.",
        )
    )

    api_commands = []
    for package_info in facts["api_client_package_roots"]:
        for script_name in package_info["scripts"]:
            if "generate" in script_name.lower():
                api_commands.append(
                    command(
                        f"Run {script_name} in {package_info['root']}",
                        package_info["root"],
                        script_command_for_package(package_info, script_name),
                    )
                )
    groups.append(
        command_group(
            "api-client-generate",
            "Run detected API client generation scripts.",
            api_commands,
            recommended=bool(api_commands and area_set & {"api-client", "web"}),
            reason=(
                "Detected package scripts that look like API client generation."
                if api_commands
                else "No API client generation scripts detected."
            ),
        )
    )

    web_commands = []
    for package_info in facts["web_package_roots"]:
        for script_name in ("lint", "test", "build"):
            if script_name in package_info["scripts"]:
                web_commands.append(
                    command(
                        f"Run {script_name} in {package_info['root']}",
                        package_info["root"],
                        script_command_for_package(package_info, script_name),
                    )
                )
    groups.append(
        command_group(
            "web-app",
            "Run detected web app lint, test, and build scripts.",
            web_commands,
            recommended=bool(web_commands and area_set & {"web", "tests"}),
            reason="Detected web package scripts." if web_commands else "No web lint/test/build scripts detected.",
        )
    )

    maui_helper_script = next(iter(facts.get("maui_helper_scripts", [])), None)
    maui_doctor_commands = []
    if facts["maui_projects"]:
        if maui_helper_script:
            maui_doctor_commands.append(
                command(
                    f"Run {maui_helper_script} doctor",
                    ".",
                    ["bash", maui_helper_script, "doctor"],
                )
            )
        else:
            maui_doctor_commands.extend(
                [
                    command("Show dotnet workloads", ".", ["dotnet", "workload", "list"]),
                    command("Show dotnet SDK info", ".", ["dotnet", "--info"]),
                ]
            )
    groups.append(
        command_group(
            "maui-android-doctor",
            "Inspect .NET MAUI Android workload availability without invoking remote CI.",
            maui_doctor_commands,
            recommended=bool(facts["maui_projects"] and "maui" in area_set),
            reason="Detected MAUI project files." if facts["maui_projects"] else "No MAUI projects detected.",
        )
    )

    maui_build_commands = []
    if maui_helper_script and facts["maui_projects"]:
        maui_build_commands.append(
            command(
                f"Run {maui_helper_script} build -c Debug",
                ".",
                ["bash", maui_helper_script, "build", "-c", "Debug"],
            )
        )
    else:
        for project in facts["maui_projects"]:
            android_frameworks = project["android_target_frameworks"]
            if android_frameworks:
                for framework in android_frameworks:
                    maui_build_commands.append(
                        command(
                            f"Build {project['path']} for {framework}",
                            ".",
                            [
                                "dotnet",
                                "build",
                                project["path"],
                                "-f",
                                framework,
                                "--no-restore",
                                "--verbosity",
                                "minimal",
                            ],
                        )
                    )
            else:
                maui_build_commands.append(
                    command(
                        f"Build {project['path']}",
                        ".",
                        ["dotnet", "build", project["path"], "--verbosity", "minimal"],
                    )
                )
    groups.append(
        command_group(
            "maui-android-build",
            "Build detected MAUI Android target frameworks locally.",
            maui_build_commands,
            recommended=bool(maui_build_commands and "maui" in area_set),
            reason="Detected MAUI Android build targets." if maui_build_commands else "No MAUI Android targets detected.",
        )
    )

    groups.append(
        command_group(
            "markdown-smoke",
            "Validate tracked Markdown files for tabs and trailing whitespace.",
            [command("Check tracked Markdown whitespace", ".", ["bash", "-lc", MARKDOWN_SMOKE_SCRIPT])]
            if facts["markdown_file_count"]
            else [],
            recommended=bool(facts["markdown_file_count"] and area_set & {"docs", "ci"}),
            reason="Detected markdown files." if facts["markdown_file_count"] else "No markdown files detected.",
        )
    )

    groups.append(
        command_group(
            "ci-manual-reference",
            "Manual reference for detected workflow files; this group intentionally does not run remote CI.",
            [],
            recommended=False,
            reason=(
                "Detected workflow files: " + ", ".join(facts["workflow_files"])
                if facts["workflow_files"]
                else "No workflow files detected."
            ),
            manual=True,
        )
    )

    return groups

def detect_android_sdk_available():
    return bool(os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))

def recommended_command_groups(
    command_groups,
    *,
    issue_text,
    changed_paths=(),
    android_sdk_available=None,
):
    if android_sdk_available is None:
        android_sdk_available = detect_android_sdk_available()

    return recommend_area_reader_command_groups(
        issue_text=issue_text,
        changed_paths=changed_paths,
        android_sdk_available=android_sdk_available,
        available_command_groups=[group["name"] for group in command_groups],
    )

def apply_recommended_command_groups(command_groups, recommendation_metadata):
    recommended = set(recommendation_metadata["recommended_command_groups"])
    for group in command_groups:
        group["recommended"] = group["name"] in recommended

def shell_function_name(group_name):
    return "group_" + group_name.replace("-", "_")

def render_verification_script(repo, command_groups):
    lines = [
        "#!/usr/bin/env bash",
        "set -Eeuo pipefail",
        "",
        "if git rev-parse --show-toplevel >/dev/null 2>&1; then",
        "  REPO_ROOT=\"$(git rev-parse --show-toplevel)\"",
        "else",
        f"  REPO_ROOT={shlex.quote(repo.as_posix())}",
        "fi",
        'cd "$REPO_ROOT"',
        "",
        "run_in() {",
        '  local dir="$1"',
        "  shift",
        '  echo "+ (${dir}) $*"',
        '  (cd "$REPO_ROOT/$dir" && "$@")',
        "}",
        "",
        "run_optional_in() {",
        '  local dir="$1"',
        "  shift",
        '  if ! run_in "$dir" "$@"; then',
        '    echo "optional command failed: $*" >&2',
        "  fi",
        "}",
        "",
    ]

    group_names = []
    for group in command_groups:
        group_names.append(group["name"])
        lines.append(f"{shell_function_name(group['name'])}() {{")
        lines.append(f"  echo {shlex.quote('== ' + group['name'] + ' ==')}")
        if group["manual"]:
            lines.append(
                "  echo "
                + shlex.quote(
                    "Manual reference only. Remote CI is not executed by this generated script."
                )
            )
            lines.append("  echo " + shlex.quote(group["reason"]))
        elif not group["commands"]:
            lines.append("  echo " + shlex.quote(group["reason"]))
        else:
            for item in group["commands"]:
                runner = "run_optional_in" if item["optional"] else "run_in"
                lines.append(
                    f"  {runner} {shlex.quote(item['cwd'])} {shlex.join(item['argv'])}"
                )
        lines.append("}")
        lines.append("")

    lines.extend(
        [
            "usage() {",
            '  echo "Usage: $0 <group|recommended|all>"',
            "  echo",
            '  echo "Groups:"',
            *[f"  echo {shlex.quote('  ' + name)}" for name in group_names],
            "}",
            "",
            "run_group() {",
            '  case "$1" in',
        ]
    )

    for group in command_groups:
        lines.append(f"    {shlex.quote(group['name'])}) {shell_function_name(group['name'])} ;;")

    lines.extend(
        [
            "    recommended)",
        ]
    )
    for group in command_groups:
        if group["recommended"]:
            lines.append(f"      {shell_function_name(group['name'])}")
    lines.extend(
        [
            "      ;;",
            "    all)",
        ]
    )
    for group in command_groups:
        if not group["manual"]:
            lines.append(f"      {shell_function_name(group['name'])}")
    lines.extend(
        [
            "      ;;",
            '    ""|-h|--help|help)',
            "      usage",
            "      ;;",
            "    *)",
            '      echo "Unknown command group: $1" >&2',
            "      usage >&2",
            "      return 2",
            "      ;;",
            "  esac",
            "}",
            "",
            'run_group "${1:-help}"',
            "",
        ]
    )
    return "\n".join(lines)

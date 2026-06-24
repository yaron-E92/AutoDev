#!/usr/bin/env python3
"""Run an area-based local Ollama reader-synthesis-coder benchmark."""

import argparse
import fnmatch
import json
import os
from pathlib import Path
import shlex
import sys
import time
from urllib import error, request
import xml.etree.ElementTree as ET


OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
DEFAULT_MAX_CHARS_PER_AREA = 50000
DEFAULT_READER_NUM_PREDICT = 1800
DEFAULT_SYNTH_NUM_PREDICT = 2200
DEFAULT_CODER_NUM_PREDICT = 2200
MAX_FILE_BYTES = 250000

SUPPORTED_AREAS = ("backend", "web", "maui", "ci", "tests", "docs", "api-client")
DEFAULT_AUTO_AREAS = ("backend", "web", "maui", "ci")

INCLUDED_SUFFIXES = {
    ".cs",
    ".csproj",
    ".sln",
    ".xaml",
    ".xml",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".props",
    ".targets",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".sh",
    ".ps1",
    ".py",
    ".toml",
    ".lock",
}

INCLUDED_FILENAMES = {
    "AGENTS.md",
    "README.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "Makefile",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "Pipfile.lock",
    "poetry.lock",
    "Directory.Build.props",
    "Directory.Packages.props",
    "MauiProgram.cs",
    "App.xaml",
    "Program.cs",
}

EXCLUDED_DIRS = {
    ".git",
    ".vs",
    ".vscode",
    "bin",
    "obj",
    "node_modules",
    ".codex-run",
    ".idea",
    ".cache",
    ".benchmark-results",
    "__pycache__",
    "TestResults",
    "dist",
    "build",
    "coverage",
}

PRIORITY_PATTERNS = (
    "AGENTS.md",
    "README.md",
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    "*.sln",
    "*.csproj",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "tsconfig*",
    "vite.config*",
    "MauiProgram.cs",
    "App.xaml",
    "Program.cs",
    "Directory.Build.props",
    "Directory.Packages.props",
    "docs/*",
    "doc/*",
    "adr/*",
    "ADRs/*",
)

AREA_HINTS = {
    "backend": {
        "keywords": ("backend", "server", "api", "database", "db", "ef", "migration"),
        "path_patterns": (
            "*backend*",
            "*server*",
            "*api*",
            "*.sln",
            "*.csproj",
            "Program.cs",
            "Directory.Build.props",
            "Directory.Packages.props",
        ),
    },
    "web": {
        "keywords": ("web", "frontend", "react", "vite", "typescript", "browser", "ui"),
        "path_patterns": (
            "*web*",
            "*frontend*",
            "*react*",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "tsconfig*",
            "vite.config*",
            "*.ts",
            "*.tsx",
            "*.js",
            "*.jsx",
            "*.css",
            "*.html",
        ),
    },
    "maui": {
        "keywords": ("maui", "mobile", "desktop", "android", "ios", "xaml"),
        "path_patterns": (
            "*maui*",
            "*mobile*",
            "*android*",
            "*ios*",
            "*.xaml",
            "MauiProgram.cs",
            "App.xaml",
        ),
    },
    "ci": {
        "keywords": ("ci", "workflow", "github actions", "build", "verify", "pipeline"),
        "path_patterns": (
            ".github/workflows/*.yml",
            ".github/workflows/*.yaml",
            "*workflow*",
            "*ci*",
            "*.sh",
            "*.ps1",
            "codex-profiles.json",
        ),
    },
    "tests": {
        "keywords": ("test", "tests", "verification", "xunit", "pytest", "playwright"),
        "path_patterns": (
            "*test*",
            "*tests*",
            "*.Tests/*",
            "*.Test/*",
            "pytest.ini",
            "playwright.config*",
        ),
    },
    "docs": {
        "keywords": ("docs", "documentation", "readme", "adr", "guide"),
        "path_patterns": (
            "README.md",
            "CONTRIBUTING.md",
            "docs/*",
            "doc/*",
            "adr/*",
            "ADRs/*",
            "*.md",
        ),
    },
    "api-client": {
        "keywords": ("api client", "client", "sdk", "http client", "openapi"),
        "path_patterns": (
            "*client*",
            "*sdk*",
            "*openapi*",
            "*swagger*",
            "*.ts",
            "*.tsx",
            "*.cs",
        ),
    },
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run an area-based local Ollama reader-synthesis-coder benchmark."
    )
    parser.add_argument("--repo", required=True, help="Repository to read for context.")
    parser.add_argument("--reader", required=True, help="Ollama area reader model name.")
    parser.add_argument(
        "--synthesizer",
        help="Ollama synthesis reader model name. Defaults to --reader.",
    )
    parser.add_argument("--coder", required=True, help="Ollama coder model name.")
    parser.add_argument("--issue", required=True, help="Issue or task text.")
    parser.add_argument(
        "--areas",
        default="auto",
        help="Area routing: auto, all, or comma-list such as backend,web,maui,ci.",
    )
    parser.add_argument(
        "--max-chars-per-area",
        type=int,
        default=DEFAULT_MAX_CHARS_PER_AREA,
        help=f"Maximum input bundle characters per area. Default: {DEFAULT_MAX_CHARS_PER_AREA}.",
    )
    parser.add_argument(
        "--reader-num-predict",
        type=int,
        default=DEFAULT_READER_NUM_PREDICT,
        help=f"Area reader num_predict option. Default: {DEFAULT_READER_NUM_PREDICT}.",
    )
    parser.add_argument(
        "--synth-num-predict",
        type=int,
        default=DEFAULT_SYNTH_NUM_PREDICT,
        help=f"Synthesis reader num_predict option. Default: {DEFAULT_SYNTH_NUM_PREDICT}.",
    )
    parser.add_argument(
        "--coder-num-predict",
        type=int,
        default=DEFAULT_CODER_NUM_PREDICT,
        help=f"Coder num_predict option. Default: {DEFAULT_CODER_NUM_PREDICT}.",
    )
    parser.add_argument("--out", required=True, help="Output directory for benchmark files.")
    return parser.parse_args()


def expand_user_path(value):
    return Path(os.path.expanduser(value)).resolve()


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_executable_text(path, text):
    write_text(path, text)
    path.chmod(path.stat().st_mode | 0o755)


def is_included_file(path):
    return path.name in INCLUDED_FILENAMES or path.suffix in INCLUDED_SUFFIXES


def iter_candidate_files(repo):
    for root, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for filename in sorted(filenames):
            path = Path(root) / filename
            if is_included_file(path):
                yield path


def matches_any(path_text, patterns):
    return any(fnmatch.fnmatch(path_text, pattern) for pattern in patterns)


def is_priority_file(relative_path):
    return matches_any(relative_path, PRIORITY_PATTERNS) or Path(relative_path).name in INCLUDED_FILENAMES


def area_for_file(relative_path, area):
    hints = AREA_HINTS[area]
    lowered = relative_path.lower()
    return matches_any(relative_path, hints["path_patterns"]) or any(
        keyword in lowered for keyword in hints["keywords"]
    )


def collect_repo_files(repo):
    files = []
    skipped_large_files = []
    skipped_unreadable_files = []

    for path in iter_candidate_files(repo):
        try:
            size = path.stat().st_size
        except OSError as exc:
            skipped_unreadable_files.append({"path": str(path), "reason": str(exc)})
            continue

        relative_path = path.relative_to(repo).as_posix()
        if size > MAX_FILE_BYTES:
            skipped_large_files.append({"path": relative_path, "bytes": size})
            continue

        areas = [area for area in SUPPORTED_AREAS if area_for_file(relative_path, area)]
        files.append(
            {
                "path": relative_path,
                "bytes": size,
                "priority": is_priority_file(relative_path),
                "areas": areas,
            }
        )

    files.sort(key=lambda item: (not item["priority"], item["path"]))
    return files, skipped_large_files, skipped_unreadable_files


def build_repo_map(repo, files, skipped_large_files, skipped_unreadable_files):
    lines = [
        f"Repository: {repo}",
        "",
        "Candidate files:",
    ]
    for item in files:
        flags = []
        if item["priority"]:
            flags.append("priority")
        if item["areas"]:
            flags.append("areas=" + ",".join(item["areas"]))
        suffix = " [" + "; ".join(flags) + "]" if flags else ""
        lines.append(f"- {item['path']} ({item['bytes']} bytes){suffix}")

    if skipped_large_files:
        lines.extend(["", "Skipped large files:"])
        for item in skipped_large_files:
            lines.append(f"- {item['path']} ({item['bytes']} bytes)")

    if skipped_unreadable_files:
        lines.extend(["", "Skipped unreadable files:"])
        for item in skipped_unreadable_files:
            lines.append(f"- {item['path']}: {item['reason']}")

    return "\n".join(lines) + "\n"


def read_json_object(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def xml_local_name(tag):
    return tag.rsplit("}", 1)[-1]


def read_csproj_facts(path):
    facts = {
        "use_maui": False,
        "target_frameworks": [],
        "android_target_frameworks": [],
    }
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ET.ParseError):
        return facts

    frameworks = []
    for element in root.iter():
        name = xml_local_name(element.tag)
        text = (element.text or "").strip()
        if name == "UseMaui" and text.lower() == "true":
            facts["use_maui"] = True
        elif name == "TargetFramework" and text:
            frameworks.append(text)
        elif name == "TargetFrameworks" and text:
            frameworks.extend(part.strip() for part in text.split(";") if part.strip())

    facts["target_frameworks"] = sorted(set(frameworks))
    facts["android_target_frameworks"] = [
        framework for framework in facts["target_frameworks"] if "android" in framework.lower()
    ]
    return facts


def package_root(package_json_path):
    parent = Path(package_json_path).parent.as_posix()
    return "." if parent == "." else parent


def package_manager_for_root(file_paths, root):
    prefix = "" if root == "." else f"{root}/"
    lockfiles = {
        "package-lock.json": ("npm", ["npm", "ci"]),
        "pnpm-lock.yaml": ("pnpm", ["pnpm", "install", "--frozen-lockfile"]),
        "yarn.lock": ("yarn", ["yarn", "install", "--frozen-lockfile"]),
        "bun.lockb": ("bun", ["bun", "install", "--frozen-lockfile"]),
        "bun.lock": ("bun", ["bun", "install", "--frozen-lockfile"]),
    }
    for lockfile, value in lockfiles.items():
        if f"{prefix}{lockfile}" in file_paths:
            return value
    return "npm", ["npm", "install"]


def detect_repo_facts(repo, files, areas, routing):
    file_paths = [item["path"] for item in files]
    file_path_set = set(file_paths)

    solutions = sorted(path for path in file_paths if path.endswith(".sln"))
    dotnet_projects = sorted(path for path in file_paths if path.endswith(".csproj"))
    workflows = sorted(
        path
        for path in file_paths
        if path.startswith(".github/workflows/")
        and (path.endswith(".yml") or path.endswith(".yaml"))
    )
    markdown_files = sorted(path for path in file_paths if path.endswith(".md"))

    csproj_facts = {}
    maui_projects = []
    for relative_path in dotnet_projects:
        facts = read_csproj_facts(repo / relative_path)
        csproj_facts[relative_path] = facts
        if facts["use_maui"] or facts["android_target_frameworks"]:
            maui_projects.append(
                {
                    "path": relative_path,
                    "target_frameworks": facts["target_frameworks"],
                    "android_target_frameworks": facts["android_target_frameworks"],
                }
            )

    package_roots = []
    for relative_path in sorted(path for path in file_paths if path.endswith("package.json")):
        root = package_root(relative_path)
        package_json = read_json_object(repo / relative_path)
        scripts = package_json.get("scripts", {})
        dependencies = package_json.get("dependencies", {})
        dev_dependencies = package_json.get("devDependencies", {})
        if not isinstance(scripts, dict):
            scripts = {}
        if not isinstance(dependencies, dict):
            dependencies = {}
        if not isinstance(dev_dependencies, dict):
            dev_dependencies = {}

        package_manager, install_command = package_manager_for_root(file_path_set, root)
        root_lower = root.lower()
        dependency_names = set(dependencies) | set(dev_dependencies)
        script_names = sorted(str(name) for name in scripts)
        is_web = (
            root_lower in {".", "web", "frontend"}
            or "web" in root_lower
            or "frontend" in root_lower
            or "vite" in dependency_names
            or "react" in dependency_names
        )
        has_api_client_generate = any("generate" in name.lower() for name in script_names) and (
            "client" in root_lower
            or "api" in root_lower
            or any("openapi" in dependency.lower() or "swagger" in dependency.lower() for dependency in dependency_names)
            or any("client" in name.lower() or "api" in name.lower() for name in script_names)
        )
        package_roots.append(
            {
                "path": relative_path,
                "root": root,
                "package_manager": package_manager,
                "install_command": install_command,
                "scripts": script_names,
                "is_web": is_web,
                "has_api_client_generate": has_api_client_generate,
            }
        )

    api_client_hints = sorted(
        path
        for path in file_paths
        if any(token in path.lower() for token in ("api-client", "apiclient", "openapi", "swagger"))
    )

    area_file_counts = {
        area: sum(1 for item in files if area in item["areas"])
        for area in SUPPORTED_AREAS
    }

    return {
        "repo": str(repo),
        "routed_areas": areas,
        "routing": routing,
        "file_count": len(files),
        "area_file_counts": area_file_counts,
        "solutions": solutions,
        "dotnet_projects": dotnet_projects,
        "csproj_facts": csproj_facts,
        "maui_projects": maui_projects,
        "package_roots": package_roots,
        "web_package_roots": [item for item in package_roots if item["is_web"]],
        "api_client_package_roots": [
            item for item in package_roots if item["has_api_client_generate"]
        ],
        "api_client_hints": api_client_hints,
        "workflow_files": workflows,
        "markdown_file_count": len(markdown_files),
        "markdown_files": markdown_files,
    }


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
    for solution in facts["solutions"]:
        dotnet_commands.append(command(f"Restore {solution}", ".", ["dotnet", "restore", solution]))
        dotnet_commands.append(
            command(
                f"Build {solution}",
                ".",
                ["dotnet", "build", solution, "--no-restore", "--verbosity", "minimal"],
            )
        )
    groups.append(
        command_group(
            "dotnet-solution",
            "Restore and build detected .NET solution files from the repository root.",
            dotnet_commands,
            recommended=bool(dotnet_commands and area_set & {"backend", "maui", "tests"}),
            reason="Detected .NET solution files." if dotnet_commands else "No .NET solution files detected.",
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

    groups.append(
        command_group(
            "maui-android-doctor",
            "Inspect .NET MAUI Android workload availability without invoking remote CI.",
            [
                command("Show dotnet workloads", ".", ["dotnet", "workload", "list"]),
                command("Show dotnet SDK info", ".", ["dotnet", "--info"]),
            ]
            if facts["maui_projects"]
            else [],
            recommended=bool(facts["maui_projects"] and "maui" in area_set),
            reason="Detected MAUI project files." if facts["maui_projects"] else "No MAUI projects detected.",
        )
    )

    maui_build_commands = []
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
            "List markdown files to verify documentation-only changes stay visible and local.",
            [command("List markdown files", ".", ["find", ".", "-name", "*.md", "-not", "-path", "./.git/*", "-print"])]
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


def recommended_command_groups(command_groups):
    recommended = []
    for group in command_groups:
        if group["recommended"]:
            recommended.append(
                {
                    "name": group["name"],
                    "reason": group["reason"],
                    "command_count": len(group["commands"]),
                }
            )
    if not any(group["name"] == "env" for group in recommended):
        recommended.insert(
            0,
            {
                "name": "env",
                "reason": "Always useful for local environment diagnostics.",
                "command_count": 0,
            },
        )
    return recommended


def shell_function_name(group_name):
    return "group_" + group_name.replace("-", "_")


def render_verification_script(repo, command_groups):
    lines = [
        "#!/usr/bin/env bash",
        "set -Eeuo pipefail",
        "",
        f"REPO_ROOT={shlex.quote(str(repo))}",
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


def route_areas(issue, areas_arg):
    requested = areas_arg.strip()
    if requested == "all":
        return list(SUPPORTED_AREAS), {
            "mode": "all",
            "matched_keywords": {},
            "defaulted": False,
        }

    if requested != "auto":
        areas = []
        for raw_area in requested.split(","):
            area = raw_area.strip()
            if not area:
                continue
            if area not in SUPPORTED_AREAS:
                raise ValueError(f"Unsupported area: {area}")
            if area not in areas:
                areas.append(area)
        if not areas:
            raise ValueError("--areas explicit list did not include any supported areas")
        return areas, {
            "mode": "explicit",
            "matched_keywords": {},
            "defaulted": False,
        }

    issue_lower = issue.lower()
    matched_keywords = {}
    areas = []
    for area in SUPPORTED_AREAS:
        keywords = [keyword for keyword in AREA_HINTS[area]["keywords"] if keyword in issue_lower]
        if keywords:
            areas.append(area)
            matched_keywords[area] = keywords

    defaulted = not areas
    if defaulted:
        areas = list(DEFAULT_AUTO_AREAS)

    return areas, {
        "mode": "auto",
        "matched_keywords": matched_keywords,
        "defaulted": defaulted,
    }


def area_file_map(files, area):
    selected = [
        item
        for item in files
        if area in item["areas"] or item["priority"]
    ]
    selected.sort(key=lambda item: (area not in item["areas"], not item["priority"], item["path"]))
    return selected


def format_area_file_map(area, files):
    lines = [f"Area file map: {area}"]
    if not files:
        lines.append("- No candidate files matched this area.")
    for item in files:
        flags = []
        if area in item["areas"]:
            flags.append("area-match")
        if item["priority"]:
            flags.append("priority")
        suffix = " [" + "; ".join(flags) + "]" if flags else ""
        lines.append(f"- {item['path']} ({item['bytes']} bytes){suffix}")
    return "\n".join(lines) + "\n"


def read_file_for_bundle(repo, relative_path):
    path = repo / relative_path
    return path.read_text(encoding="utf-8", errors="replace")


def build_area_bundle(repo, area, issue, repo_map, files, max_chars):
    if max_chars <= 0:
        raise ValueError("--max-chars-per-area must be greater than zero")

    file_map_text = format_area_file_map(area, files)
    header = f"""Issue:
{issue}

Routed area: {area}

Area hint keywords:
{", ".join(AREA_HINTS[area]["keywords"])}

{file_map_text}
Repository map:
{repo_map}
File excerpts:
"""
    parts = [header]
    remaining = max_chars - len(header)
    included_files = []
    skipped_unreadable_files = []
    truncated = remaining < 0

    if remaining > 0:
        for item in files:
            relative_path = item["path"]
            try:
                content = read_file_for_bundle(repo, relative_path)
            except OSError as exc:
                skipped_unreadable_files.append({"path": relative_path, "reason": str(exc)})
                continue

            entry = f"\n\n===== FILE: {relative_path} =====\n{content.rstrip()}\n"
            if len(entry) > remaining:
                if remaining > len(f"\n\n===== FILE: {relative_path} =====\n"):
                    parts.append(entry[:remaining])
                    included_files.append(relative_path)
                truncated = True
                break

            parts.append(entry)
            included_files.append(relative_path)
            remaining -= len(entry)
            if remaining <= 0:
                truncated = True
                break

    bundle = "".join(parts)
    metadata = {
        "area": area,
        "max_chars": max_chars,
        "bundle_chars": len(bundle),
        "candidate_file_count": len(files),
        "included_file_count": len(included_files),
        "included_files": included_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "truncated": truncated,
        "placeholder_or_absent": not any(area in item["areas"] for item in files),
    }
    return bundle, metadata, file_map_text


def build_area_reader_prompt(issue, area, bundle, metadata):
    return f"""You are the area reader model for area: {area}.

You are not the coder. Do not edit files. Do not design a patch. Read only the provided repository context and produce a factual handoff brief for a later synthesis reader and coder.

Your brief must:
- Include exact file paths for every repository fact you mention.
- Distinguish visible facts from inference.
- Stay factual; do not invent shell commands or implementation steps.
- Name local verification needs for this area conceptually, not as freehand command lines.
- Identify uncertainties and missing files.
- If this area is placeholder-only or not actually present, say so clearly.

Original issue:
{issue}

Area bundle metadata:
{json.dumps(metadata, indent=2, sort_keys=True)}

Area input bundle:
{bundle}
"""


def build_synthesis_prompt(issue, areas, area_results, detected_facts, command_groups):
    brief_blocks = []
    for result in area_results:
        brief_blocks.append(
            f"""## Area: {result['area']}

Reader metadata:
{json.dumps(result['metadata'], indent=2, sort_keys=True)}

Reader brief:
{result['brief']}
"""
        )

    return f"""You are the synthesis reader model in an area-based local LLM benchmark.

You are not the coder. Combine the area reader briefs into one compact coder handoff.

Your handoff must:
- Preserve area-specific details.
- List routed areas.
- List repo/application surfaces.
- List relevant files by area.
- Use the deterministic facts below as the source of truth for repository structure.
- Refer to named verification command groups instead of inventing shell commands.
- Include cross-area risks.
- Include constraints and uncertainties.
- Do not invent files or commands.

Original issue:
{issue}

Routed areas:
{", ".join(areas)}

Deterministic repository facts:
{json.dumps(detected_facts, indent=2, sort_keys=True)}

Available verification command groups:
{json.dumps(command_groups, indent=2, sort_keys=True)}

Area reader briefs:
{"".join(brief_blocks)}
"""


def build_coder_prompt(issue, synthesis_brief, detected_facts, recommended_groups, command_groups):
    return f"""You are the coder model in an area-based local LLM benchmark.

Consume the original issue and the synthesized handoff. Produce a minimal issue-scoped implementation or verification plan.

Rules:
- For verification-only issues, list "files to inspect," not "files likely needing changes."
- Name exact files only when supported by the handoff.
- Select verification by named command group from the deterministic command group list.
- Do not write freehand shell commands.
- Do not use placeholder commands.
- Do not invent test projects or paths.
- Do not refactor unrelated code.
- Be strict about uncertainty.

Original issue:
{issue}

Synthesized handoff:
{synthesis_brief}

Deterministic repository facts:
{json.dumps(detected_facts, indent=2, sort_keys=True)}

Recommended verification command groups:
{json.dumps(recommended_groups, indent=2, sort_keys=True)}

All available verification command groups:
{json.dumps(command_groups, indent=2, sort_keys=True)}
"""


def call_ollama(model, prompt, num_predict):
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {
            "num_predict": num_predict,
        },
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        OLLAMA_CHAT_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.monotonic()
    try:
        with request.urlopen(http_request) as response:
            response_body = response.read().decode("utf-8")
    except error.URLError as exc:
        raise RuntimeError(f"Ollama request failed for model {model}: {exc}") from exc
    wall_seconds = time.monotonic() - started

    try:
        raw = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON for model {model}: {exc}") from exc

    return raw, wall_seconds


def duration_seconds(raw, key):
    value = raw.get(key)
    if not isinstance(value, (int, float)):
        return 0.0
    return value / 1_000_000_000


def tokens_per_sec(count, seconds):
    if not isinstance(count, (int, float)) or count <= 0 or seconds <= 0:
        return 0.0
    return count / seconds


def extract_message(raw):
    message = raw.get("message")
    if not isinstance(message, dict):
        return "", ""

    content = message.get("content")
    thinking = message.get("thinking")
    return (
        content if isinstance(content, str) else "",
        thinking if isinstance(thinking, str) else "",
    )


def build_metrics(raw, wall_seconds, response_text):
    prompt_eval_count = raw.get("prompt_eval_count", 0)
    eval_count = raw.get("eval_count", 0)
    prompt_eval_seconds = duration_seconds(raw, "prompt_eval_duration")
    eval_seconds = duration_seconds(raw, "eval_duration")

    return {
        "done_reason": raw.get("done_reason", ""),
        "wall_seconds": wall_seconds,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_tokens_per_sec": tokens_per_sec(prompt_eval_count, prompt_eval_seconds),
        "eval_count": eval_count,
        "generation_tokens_per_sec": tokens_per_sec(eval_count, eval_seconds),
        "total_duration_seconds": duration_seconds(raw, "total_duration"),
        "load_duration_seconds": duration_seconds(raw, "load_duration"),
        "response_chars": len(response_text),
    }


def run_area_reader(args, repo, out, area, repo_map, files):
    area_dir = out / f"area-{area}"
    selected_files = area_file_map(files, area)
    bundle, metadata, file_map_text = build_area_bundle(
        repo,
        area,
        args.issue,
        repo_map,
        selected_files,
        args.max_chars_per_area,
    )
    reader_prompt = build_area_reader_prompt(args.issue, area, bundle, metadata)

    write_text(area_dir / "file-map.txt", file_map_text)
    write_text(area_dir / "input-bundle.txt", bundle)
    write_text(area_dir / "reader-prompt.txt", reader_prompt)

    raw, wall_seconds = call_ollama(args.reader, reader_prompt, args.reader_num_predict)
    brief, thinking = extract_message(raw)
    metrics = build_metrics(raw, wall_seconds, brief)

    write_json(area_dir / "reader-raw.json", raw)
    write_text(area_dir / "reader-brief.md", brief)
    write_text(area_dir / "reader-thinking.md", thinking)
    write_json(area_dir / "metrics.json", metrics)

    reader_error = ""
    if metrics["done_reason"] == "length" and len(brief) < 500:
        reader_error = (
            "Area reader response ended because of length with very little output; "
            "the prompt likely filled the context window. Lower --max-chars-per-area "
            "or use a reader model with a larger context window."
        )
        write_text(area_dir / "reader-error.txt", reader_error + "\n")
        print(f"{area}: {reader_error}", file=sys.stderr)

    return {
        "area": area,
        "brief": brief,
        "metrics": metrics,
        "metadata": metadata,
        "reader_error": reader_error,
    }


def main():
    args = parse_args()
    if args.synthesizer is None:
        args.synthesizer = args.reader

    repo = expand_user_path(args.repo)
    out = expand_user_path(args.out)

    if not repo.is_dir():
        print(f"--repo is not a directory: {repo}", file=sys.stderr)
        return 2

    try:
        areas, routing_detail = route_areas(args.issue, args.areas)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.max_chars_per_area <= 0:
        print("--max-chars-per-area must be greater than zero", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    write_text(out / "issue.txt", args.issue + "\n")

    files, skipped_large_files, skipped_unreadable_files = collect_repo_files(repo)
    repo_map = build_repo_map(repo, files, skipped_large_files, skipped_unreadable_files)
    write_text(out / "repo-map.txt", repo_map)

    routing = {
        "requested": args.areas,
        "areas": areas,
        "supported_areas": list(SUPPORTED_AREAS),
        "detail": routing_detail,
    }
    write_json(out / "routing.json", routing)

    detected_facts = detect_repo_facts(repo, files, areas, routing)
    command_groups = build_verification_command_groups(detected_facts, areas)
    recommended_groups = recommended_command_groups(command_groups)
    write_json(out / "detected-facts.json", detected_facts)
    write_json(out / "verification-command-groups.json", command_groups)
    write_json(out / "recommended-command-groups.json", recommended_groups)
    write_executable_text(
        out / "verification-commands.sh",
        render_verification_script(repo, command_groups),
    )

    area_results = []
    for area in areas:
        try:
            area_results.append(run_area_reader(args, repo, out, area, repo_map, files))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    synthesis_prompt = build_synthesis_prompt(
        args.issue,
        areas,
        area_results,
        detected_facts,
        command_groups,
    )
    write_text(out / "synthesis-prompt.txt", synthesis_prompt)
    try:
        synthesis_raw, synthesis_wall_seconds = call_ollama(
            args.synthesizer,
            synthesis_prompt,
            args.synth_num_predict,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    synthesis_brief, synthesis_thinking = extract_message(synthesis_raw)
    synthesis_metrics = build_metrics(synthesis_raw, synthesis_wall_seconds, synthesis_brief)
    write_json(out / "synthesis-raw.json", synthesis_raw)
    write_text(out / "synthesis-brief.md", synthesis_brief)
    write_text(out / "synthesis-thinking.md", synthesis_thinking)
    write_json(out / "synthesis-metrics.json", synthesis_metrics)

    coder_prompt = build_coder_prompt(
        args.issue,
        synthesis_brief,
        detected_facts,
        recommended_groups,
        command_groups,
    )
    write_text(out / "coder-prompt.txt", coder_prompt)
    try:
        coder_raw, coder_wall_seconds = call_ollama(
            args.coder,
            coder_prompt,
            args.coder_num_predict,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    coder_plan, coder_thinking = extract_message(coder_raw)
    coder_metrics = build_metrics(coder_raw, coder_wall_seconds, coder_plan)
    write_json(out / "coder-raw.json", coder_raw)
    write_text(out / "coder-plan.md", coder_plan)
    write_text(out / "coder-thinking.md", coder_thinking)
    write_json(out / "coder-metrics.json", coder_metrics)

    area_metrics = {result["area"]: result["metrics"] for result in area_results}
    summary = {
        "repo": str(repo),
        "out": str(out),
        "reader": args.reader,
        "synthesizer": args.synthesizer,
        "coder": args.coder,
        "max_chars_per_area": args.max_chars_per_area,
        "areas": areas,
        "routing": routing,
        "repo_file_count": len(files),
        "skipped_large_files": skipped_large_files,
        "skipped_unreadable_files": skipped_unreadable_files,
        "detected_facts": detected_facts,
        "recommended_command_groups": recommended_groups,
        "verification_command_groups": command_groups,
        "area_metadata": {result["area"]: result["metadata"] for result in area_results},
        "area_metrics": area_metrics,
        "area_errors": {
            result["area"]: result["reader_error"]
            for result in area_results
            if result["reader_error"]
        },
        "synthesis_metrics": synthesis_metrics,
        "coder_metrics": coder_metrics,
        "outputs": [
            "issue.txt",
            "repo-map.txt",
            "routing.json",
            "detected-facts.json",
            "recommended-command-groups.json",
            "verification-command-groups.json",
            "verification-commands.sh",
            *[
                f"area-{area}/{name}"
                for area in areas
                for name in (
                    "file-map.txt",
                    "input-bundle.txt",
                    "reader-prompt.txt",
                    "reader-brief.md",
                    "reader-raw.json",
                    "reader-thinking.md",
                    "metrics.json",
                )
            ],
            "synthesis-prompt.txt",
            "synthesis-brief.md",
            "synthesis-raw.json",
            "synthesis-thinking.md",
            "synthesis-metrics.json",
            "coder-prompt.txt",
            "coder-plan.md",
            "coder-raw.json",
            "coder-thinking.md",
            "coder-metrics.json",
            "summary.json",
        ],
    }
    write_json(out / "summary.json", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())

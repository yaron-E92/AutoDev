from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from area_reader.settings import (
    AREA_HINTS,
    EXCLUDED_DIRS,
    GENERATED_DIRS,
    INCLUDED_FILENAMES,
    INCLUDED_SUFFIXES,
    MAX_FILE_BYTES,
    PRIORITY_PATTERNS,
    SUPPORTED_AREAS,
)

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

def is_generated_relative_path(relative_path):
    parts = tuple(part.casefold() for part in Path(str(relative_path).replace("\\", "/")).parts)
    generated = {name.casefold() for name in GENERATED_DIRS}
    return any(part in generated for part in parts)

def source_package_manifest_paths(repo, file_paths, runner=subprocess.run):
    candidates = sorted(
        path
        for path in file_paths
        if path.endswith("package.json") and not is_generated_relative_path(path)
    )
    if not candidates:
        return set()

    try:
        completed = runner(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", *candidates],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
    except (OSError, TypeError):
        return set(candidates)

    if int(getattr(completed, "returncode", 1)) != 0:
        return set(candidates)
    return {
        line.strip().replace("\\", "/")
        for line in str(getattr(completed, "stdout", "") or "").splitlines()
        if line.strip()
    }

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
    solution_filters = sorted(path for path in file_paths if path.endswith(".slnf"))
    dotnet_projects = sorted(path for path in file_paths if path.endswith(".csproj"))
    workflows = sorted(
        path
        for path in file_paths
        if path.startswith(".github/workflows/")
        and (path.endswith(".yml") or path.endswith(".yaml"))
    )
    markdown_files = sorted(path for path in file_paths if path.endswith(".md"))
    maui_helper_scripts = sorted(
        path
        for path in file_paths
        if path.endswith(".sh") and "maui" in path.lower() and "android" in path.lower()
    )

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
    source_package_manifests = source_package_manifest_paths(repo, file_paths)
    for relative_path in sorted(source_package_manifests):
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
        "solution_filters": solution_filters,
        "dotnet_projects": dotnet_projects,
        "csproj_facts": csproj_facts,
        "maui_projects": maui_projects,
        "maui_helper_scripts": maui_helper_scripts,
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

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from area_reader import repository as area_reader_repository
from area_reader import routing as area_reader_routing
from area_reader import verification as area_reader_verification
from automation import run_manifest
from automation.workflow_storage import read_json, read_text, write_json


REFRESHABLE_DISCOVERY_ARTIFACTS = (
    "detected-facts.json",
    "verification-command-groups.json",
    "recommended-command-groups.json",
)


def _routed_areas(current: Path, issue_text: str, *, preserve_existing: bool) -> tuple[list[str], dict[str, object]]:
    if preserve_existing:
        routed = read_json(current / "routed-areas.json")
        if isinstance(routed, dict):
            areas = [
                str(value)
                for value in routed.get("areas", [])
                if isinstance(value, str) and value
            ]
            if areas:
                routing = {key: value for key, value in routed.items() if key != "areas"}
                return areas, routing
    return area_reader_routing.route_areas(issue_text, "auto")


def refresh_verification_discovery(
    repo: Path,
    current: Path,
    *,
    issue_text: str = "",
    changed_paths: Iterable[str] = (),
    preserve_routing: bool = True,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    issue_text = issue_text or read_text(current / "issue.md")

    files, skipped_large, skipped_unreadable = area_reader_repository.collect_repo_files(repo)
    repo_map = area_reader_repository.build_repo_map(
        repo,
        files,
        skipped_large,
        skipped_unreadable,
    )
    areas, routing = _routed_areas(
        current,
        issue_text,
        preserve_existing=preserve_routing,
    )
    facts = area_reader_repository.detect_repo_facts(repo, files, areas, routing)
    groups = area_reader_verification.build_verification_command_groups(facts, areas)
    recommendations = area_reader_verification.recommended_command_groups(
        groups,
        issue_text=issue_text,
        changed_paths=changed_paths,
    )
    area_reader_verification.apply_recommended_command_groups(groups, recommendations)

    if not preserve_routing or not (current / "routed-areas.json").is_file():
        write_json(current / "routed-areas.json", {"areas": areas, **routing})
    write_json(current / "detected-facts.json", facts)
    write_json(current / "verification-command-groups.json", groups)
    write_json(current / "recommended-command-groups.json", recommendations)

    manifest_path = current / "run-manifest.json"
    if manifest_path.is_file():
        try:
            run_manifest.mark_stage_artifacts_refreshable(
                manifest_path,
                "repository-read",
                REFRESHABLE_DISCOVERY_ARTIFACTS,
            )
        except run_manifest.ManifestError:
            pass

    return {
        "files": files,
        "skipped_large": skipped_large,
        "skipped_unreadable": skipped_unreadable,
        "repo_map": repo_map,
        "areas": areas,
        "routing": routing,
        "facts": facts,
        "groups": groups,
        "recommendations": recommendations,
    }


def stale_verification_reason(repo: Path, current: Path) -> str:
    repo = repo.expanduser().resolve()
    facts = read_json(current / "detected-facts.json")
    if isinstance(facts, dict):
        roots = facts.get("package_roots", [])
        if isinstance(roots, list):
            for item in roots:
                if not isinstance(item, dict):
                    continue
                root = str(item.get("root", "") or ".")
                if area_reader_repository.is_generated_relative_path(root):
                    return f"generated package root is recorded in deterministic verification discovery: {root}"

    groups = read_json(current / "verification-command-groups.json")
    recommendations = read_json(current / "recommended-command-groups.json")
    if not isinstance(groups, list) or not isinstance(recommendations, dict):
        return "deterministic verification discovery artifacts are missing or invalid"

    recommended = {
        str(value)
        for value in recommendations.get("recommended_command_groups", [])
        if isinstance(value, str) and value
    }
    for group in groups:
        if not isinstance(group, dict) or str(group.get("name", "")) not in recommended:
            continue
        commands = group.get("commands", [])
        if not isinstance(commands, list):
            return f"recommended verification group has invalid commands: {group.get('name', '')}"
        for command in commands:
            if not isinstance(command, dict):
                continue
            raw_cwd = str(command.get("cwd", ".") or ".").replace("\\", "/")
            if area_reader_repository.is_generated_relative_path(raw_cwd):
                return f"recommended verification command points into generated output: {raw_cwd}"
            candidate = (repo / raw_cwd).resolve()
            try:
                candidate.relative_to(repo)
            except ValueError:
                continue
            if not candidate.is_dir():
                return f"recommended verification command cwd no longer exists: {raw_cwd}"
    return ""


def refresh_stale_verification_discovery(
    repo: Path,
    current: Path,
    *,
    issue_text: str = "",
) -> str:
    reason = stale_verification_reason(repo, current)
    if not reason:
        return ""
    refresh_verification_discovery(
        repo,
        current,
        issue_text=issue_text,
        preserve_routing=True,
    )
    remaining = stale_verification_reason(repo, current)
    if remaining:
        raise ValueError(
            "deterministic verification discovery remains stale after refresh: " + remaining
        )
    return reason

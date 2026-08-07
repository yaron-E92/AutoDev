from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from area_reader_v2 import runner_core as area_reader_core
from automation import run_real_issue_core as run_core
from automation.model_output_sanitizer import sanitize_model_output
from automation.model_providers import load_provider_config
from automation.prompt_policies import compose_prompt, resolve_prompt_policies
from automation.prompt_runner import PromptRunnerError, handle_planner_output
from automation.semantic_verifier import (
    SemanticVerifierError,
    build_semantic_prompt,
    collect_changed_files,
    collect_current_diff,
    collect_deterministic_evidence,
    parse_semantic_output,
    render_template,
    write_final_verdict,
    write_semantic_result,
)


AUTODEV_ROOT = Path(__file__).resolve().parents[1]
CURRENT_DIR = Path(".codex-run") / "current"
COMMAND_FILES = (
    "autodev-read.md",
    "autodev-plan.md",
    "autodev-implement.md",
    "autodev-fix.md",
    "autodev-verify.md",
)
AGENT_FILES = (
    "autodev-reader.md",
    "autodev-synthesizer.md",
    "autodev-planner.md",
    "autodev-implementer.md",
    "autodev-fixer.md",
    "autodev-verifier.md",
)
MAX_HANDOFF_CHARS = 30_000
MAX_READER_BUNDLE_CHARS = 24_000


class OpenCodeAdapterError(RuntimeError):
    pass


def install_assets(
    target_repo: Path,
    autodev_root: Path = AUTODEV_ROOT,
    *,
    python_command: str = "python",
) -> list[Path]:
    target_repo = target_repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    if not target_repo.is_dir():
        raise OpenCodeAdapterError(f"target repository is not a directory: {target_repo}")

    source = autodev_root / "integrations" / "opencode"
    target = target_repo / ".opencode"
    installed: list[Path] = []
    for directory, names in (("commands", COMMAND_FILES), ("agents", AGENT_FILES)):
        destination = target / directory
        destination.mkdir(parents=True, exist_ok=True)
        for name in names:
            source_file = source / directory / name
            if not source_file.is_file():
                raise OpenCodeAdapterError(f"missing canonical OpenCode asset: {source_file}")
            target_file = destination / name
            shutil.copyfile(source_file, target_file)
            installed.append(target_file)

    wrapper_source = source / "autodev.ps1"
    wrapper_target = target / "autodev.ps1"
    if not wrapper_source.is_file():
        raise OpenCodeAdapterError(f"missing canonical OpenCode bridge wrapper: {wrapper_source}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wrapper_source, wrapper_target)
    installed.append(wrapper_target)

    config_path = target / "autodev.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "autodev_root": str(autodev_root),
                "python": python_command,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    installed.append(config_path)
    return installed


def issue_number_from_arguments(arguments: str) -> int:
    match = re.search(r"(?<!\d)#?(\d+)(?!\d)", arguments or "")
    return int(match.group(1)) if match else 0


def ensure_current_issue(
    repo: Path,
    autodev_root: Path,
    arguments: str,
    *,
    runner=subprocess.run,
) -> Path:
    current = repo / CURRENT_DIR
    requested_issue = issue_number_from_arguments(arguments)
    state = _read_json(current / "state.json")
    current_issue = int(state.get("IssueNumber", 0) or 0) if isinstance(state, dict) else 0
    if current.is_dir() and (requested_issue == 0 or requested_issue == current_issue):
        return current
    if requested_issue == 0:
        raise OpenCodeAdapterError(
            "no prepared AutoDev issue is available; pass an issue number to the OpenCode command"
        )

    workflow = autodev_root / "windows" / "scripts" / "issue-to-pr-cycle.ps1"
    command = [
        "pwsh",
        "-NoProfile",
        "-File",
        str(workflow),
        "-Mode",
        "Prepare",
        "-WorkingDirectory",
        str(repo),
        "-Issue",
        str(requested_issue),
        "-ForceCurrent",
        "-PromptDir",
        str(autodev_root / "promptTemplates"),
        "-ProfilesPath",
        str(autodev_root / "codex-profiles.json"),
    ]
    env = dict(os.environ)
    for name in (
        "PROVIDER_PROFILE",
        "PLANNER_PROVIDER",
        "PLANNER_MODEL",
        "PLANNER_AGENT_COMMAND",
        "AGENT_PROVIDER",
        "AGENT_MODEL",
    ):
        env.pop(name, None)
    completed = runner(command, cwd=repo, env=env, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "Prepare failed.").strip()
        raise OpenCodeAdapterError(f"AutoDev Prepare failed: {detail}")
    state = _read_json(current / "state.json")
    if not isinstance(state, dict) or int(state.get("IssueNumber", 0) or 0) != requested_issue:
        raise OpenCodeAdapterError("AutoDev Prepare did not create the requested current issue state")
    return current


def prepare_role(
    role: str,
    repo: Path,
    arguments: str,
    *,
    autodev_root: Path = AUTODEV_ROOT,
) -> Path:
    repo = repo.expanduser().resolve()
    autodev_root = autodev_root.expanduser().resolve()
    current = ensure_current_issue(repo, autodev_root, arguments)
    state = _read_state(current)
    issue_text = _read_text(current / "issue.md") or str(state.get("IssueText", ""))
    policies = _resolved_policies(repo, state)

    if role == "reader":
        prompt = _prepare_reader(repo, current, issue_text)
        path = current / "reader.md"
    elif role == "synthesizer":
        prompt = _prepare_synthesizer(current, issue_text)
        path = current / "synthesizer.md"
    elif role == "planner":
        prompt = run_core.build_planner_prompt_from_area_reader(
            current,
            issue_text,
            str(state.get("LocalCheck", "")),
            [str(value) for value in state.get("Labels", [])] if isinstance(state.get("Labels"), list) else [],
            str(state.get("StackContext", "")),
        )
        path = current / "planner.md"
    elif role == "implementer":
        prompt = render_template(
            _read_text(autodev_root / "promptTemplates" / "implementer.md"),
            {
                "StackContext": str(state.get("StackContext", "")),
                "LocalCheck": str(state.get("LocalCheck", "")),
                "Plan": _plan_text(current),
                "IssueText": issue_text,
            },
        )
        path = current / "implementer.md"
    elif role == "fixer":
        source = _fixer_source(current, arguments)
        prompt = _read_text(source)
        if not prompt.strip():
            raise OpenCodeAdapterError(f"fixer source artifact is empty: {source}")
        path = current / "fixer.md"
    elif role == "verifier":
        changed_files = collect_changed_files(repo)
        prompt = build_semantic_prompt(
            issue_text=issue_text,
            synthesized_handoff=_read_text(current / "synthesized-handoff.md"),
            plan=_plan_text(current),
            changed_files=changed_files,
            diff=collect_current_diff(repo, changed_files),
            deterministic_evidence=collect_deterministic_evidence(current),
            uncertainty_notes=_read_text(current / "verification-notes.md"),
            template=_read_text(autodev_root / "promptTemplates" / "semantic-verifier.md"),
        )
        path = current / "verifier.md"
    else:
        raise OpenCodeAdapterError(f"unsupported OpenCode role: {role}")

    effective = compose_prompt(role, prompt, policies[role])
    _write_text(path, effective)
    return path


def accept_role(role: str, repo: Path, input_path: Path | None = None) -> list[Path]:
    repo = repo.expanduser().resolve()
    current = repo / CURRENT_DIR
    if not current.is_dir():
        raise OpenCodeAdapterError(".codex-run/current is missing; prepare the role first")

    if role == "reader":
        source = input_path or current / "reader-brief.md"
        text = _bounded_result(source)
        reader_path = current / "reader-brief.md"
        handoff_path = current / "synthesized-handoff.md"
        _write_text(reader_path, text + "\n")
        _write_text(handoff_path, text + "\n")
        return [reader_path, handoff_path]
    if role == "synthesizer":
        source = input_path or current / "synthesized-handoff.md"
        text = _bounded_result(source)
        handoff_path = current / "synthesized-handoff.md"
        _write_text(handoff_path, text + "\n")
        return [handoff_path]
    if role == "planner":
        source = input_path or current / "plan.md"
        output = _bounded_result(source)
        target = current / "plan.md"
        handle_planner_output(output, target)
        return [target]
    if role == "implementer":
        target = current / "commit-message.txt"
        message = sanitize_model_output(_read_text(target)).splitlines()
        if not message or not message[0].strip():
            raise OpenCodeAdapterError("implementer must write .codex-run/current/commit-message.txt")
        _write_text(target, message[0].strip()[:200] + "\n")
        return [target]
    if role == "fixer":
        return []
    if role == "verifier":
        source = input_path or current / "verification-result.json"
        result = parse_semantic_output(_read_text(source))
        result_path = current / "verification-result.json"
        _write_text(result_path, json.dumps(result, indent=2, sort_keys=True) + "\n")
        attempt_path = write_semantic_result(current, 0, result)
        final_path = write_final_verdict(current, result)
        return [result_path, attempt_path, final_path]
    raise OpenCodeAdapterError(f"unsupported OpenCode role: {role}")


def _prepare_reader(repo: Path, current: Path, issue_text: str) -> str:
    files, skipped_large, skipped_unreadable = area_reader_core.collect_repo_files(repo)
    repo_map = area_reader_core.build_repo_map(repo, files, skipped_large, skipped_unreadable)
    areas, routing = area_reader_core.route_areas(issue_text, "auto")
    facts = area_reader_core.detect_repo_facts(repo, files, areas, routing)
    groups = area_reader_core.build_verification_command_groups(facts, areas)
    recommendations = area_reader_core.recommended_command_groups(
        groups,
        issue_text=issue_text,
        changed_paths=(),
    )
    area_reader_core.apply_recommended_command_groups(groups, recommendations)
    _write_json(current / "routed-areas.json", {"areas": areas, **routing})
    _write_json(current / "detected-facts.json", facts)
    _write_json(current / "verification-command-groups.json", groups)
    _write_json(current / "recommended-command-groups.json", recommendations)

    bundle, included = _bounded_reader_bundle(repo, files, areas, repo_map)
    metadata = {
        "routed_areas": areas,
        "included_files": included,
        "bundle_chars": len(bundle),
        "max_chars": MAX_READER_BUNDLE_CHARS,
        "truncated": len(bundle) >= MAX_READER_BUNDLE_CHARS,
    }
    return area_reader_core.build_area_reader_prompt(
        issue_text,
        ",".join(areas) or "repository",
        bundle,
        metadata,
    )


def _bounded_reader_bundle(
    repo: Path,
    files: list[dict[str, object]],
    areas: list[str],
    repo_map: str,
) -> tuple[str, list[str]]:
    header = (
        "Routed areas: "
        + ", ".join(areas)
        + "\n\nRepository map:\n"
        + repo_map[:8_000]
        + "\nRelevant file excerpts:\n"
    )
    parts = [header]
    included: list[str] = []
    remaining = MAX_READER_BUNDLE_CHARS - len(header)
    for item in files:
        item_areas = item.get("areas", [])
        if not item.get("priority") and not any(area in item_areas for area in areas):
            continue
        relative = str(item.get("path", ""))
        if not relative:
            continue
        try:
            content = area_reader_core.read_file_for_bundle(repo, relative)
        except OSError:
            continue
        block = f"\n===== FILE: {relative} =====\n{content.rstrip()}\n"
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining]
        parts.append(block)
        included.append(relative)
        remaining -= len(block)
    return "".join(parts)[:MAX_READER_BUNDLE_CHARS], included


def _prepare_synthesizer(current: Path, issue_text: str) -> str:
    brief = _read_text(current / "reader-brief.md") or _read_text(current / "synthesized-handoff.md")
    if not brief.strip():
        raise OpenCodeAdapterError("reader output is missing; run /autodev-read first")
    routed = _read_json(current / "routed-areas.json")
    areas = [str(value) for value in routed.get("areas", [])] if isinstance(routed, dict) else []
    facts = _read_json(current / "detected-facts.json")
    groups = _read_json(current / "verification-command-groups.json")
    return area_reader_core.build_synthesis_prompt(
        issue_text,
        areas,
        [{"area": "opencode-reader", "brief": brief[:MAX_HANDOFF_CHARS], "metadata": {"source": "reader-brief.md"}}],
        facts,
        groups,
    )


def _fixer_source(current: Path, arguments: str) -> Path:
    lowered = (arguments or "").casefold()
    preferred: list[Path] = []
    if "semantic" in lowered or "verifier" in lowered:
        preferred.append(current / "verification-repair.md")
    if "ci" in lowered:
        preferred.append(current / "ci-repair.md")
    if "local" in lowered or "deterministic" in lowered:
        preferred.append(current / "local-repair.md")
    preferred.extend(
        [
            current / "verification-repair.md",
            current / "local-repair.md",
            current / "ci-repair.md",
        ]
    )
    existing = []
    seen: set[Path] = set()
    for path in preferred:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        existing.append(path)
    if not existing:
        raise OpenCodeAdapterError(
            "no repair artifact is available; use the existing AutoDev verification/local-check stage first"
        )
    if not any(token in lowered for token in ("semantic", "verifier", "ci", "local", "deterministic")):
        existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return existing[0]


def _resolved_policies(repo: Path, state: dict[str, object]) -> dict[str, str]:
    profile_value = str(state.get("ProviderProfile", "")).strip()
    if not profile_value:
        return resolve_prompt_policies({})
    profile = Path(profile_value).expanduser()
    if not profile.is_absolute():
        profile = repo / profile
    try:
        config = load_provider_config(str(profile))
    except (OSError, json.JSONDecodeError):
        config = {}
    return resolve_prompt_policies(config)


def _plan_text(current: Path) -> str:
    return _read_text(current / "plan.md") or _read_text(current / "coder-plan.md")


def _bounded_result(path: Path) -> str:
    value = sanitize_model_output(_read_text(path))
    if not value:
        raise OpenCodeAdapterError(f"role result is empty: {path}")
    if len(value) > MAX_HANDOFF_CHARS:
        raise OpenCodeAdapterError(
            f"role result exceeds the {MAX_HANDOFF_CHARS}-character AutoDev handoff limit: {path}"
        )
    return value


def _read_state(current: Path) -> dict[str, object]:
    state = _read_json(current / "state.json")
    if not isinstance(state, dict):
        raise OpenCodeAdapterError(".codex-run/current/state.json is missing or invalid")
    return state


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    _write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thin OpenCode frontend for existing AutoDev role artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--target-repo", default=".")
    install.add_argument("--autodev-root", default=str(AUTODEV_ROOT))
    install.add_argument("--python", default=os.environ.get("PYTHON", "python"))

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--role", choices=("reader", "synthesizer", "planner", "implementer", "fixer", "verifier"), required=True)
    prepare.add_argument("--repo", default=".")
    prepare.add_argument("--arguments", default="")
    prepare.add_argument("--autodev-root", default=str(AUTODEV_ROOT))

    accept = subparsers.add_parser("accept")
    accept.add_argument("--role", choices=("reader", "synthesizer", "planner", "implementer", "fixer", "verifier"), required=True)
    accept.add_argument("--repo", default=".")
    accept.add_argument("--input", default="")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            installed = install_assets(
                Path(args.target_repo),
                Path(args.autodev_root),
                python_command=args.python,
            )
            print(f"Installed {len(installed)} AutoDev OpenCode assets into {Path(args.target_repo).resolve() / '.opencode'}")
            return 0
        if args.command == "prepare":
            path = prepare_role(
                args.role,
                Path(args.repo),
                args.arguments,
                autodev_root=Path(args.autodev_root),
            )
            print(path)
            return 0
        if args.command == "accept":
            paths = accept_role(
                args.role,
                Path(args.repo),
                Path(args.input) if args.input else None,
            )
            for path in paths:
                print(path)
            return 0
    except (OpenCodeAdapterError, PromptRunnerError, SemanticVerifierError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 1


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())

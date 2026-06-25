from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, TextIO


DEFAULT_CREATION_LOG = Path(".codex-run") / "issue-creation-log.jsonl"
MIN_DESCRIPTION_CHARS = 12


@dataclass(frozen=True)
class IssueDraft:
    title: str
    body: str
    labels: list[str]


@dataclass(frozen=True)
class RepoSelection:
    repository: str | None
    ambiguous: bool
    candidates: list[str]


def split_descriptions(text: str) -> list[str]:
    descriptions: list[str] = []
    current: list[str] = []

    def flush() -> None:
        value = "\n".join(current).strip()
        if value:
            descriptions.append(value)
        current.clear()

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip() == "---":
            flush()
            continue
        if re.match(r"^#{1,6}\s+\S", line):
            flush()
            continue
        current.append(line)

    flush()
    return descriptions


def build_issue_draft(description: str) -> IssueDraft:
    cleaned = " ".join(description.strip().split())
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0].strip(" .!?")
    words = first_sentence.split()
    title = " ".join(words[:7]).strip()
    if not title:
        title = "Create automation issue"

    labels = suggest_labels(cleaned)
    body = "\n".join(
        [
            "## Context",
            cleaned,
            "",
            "## Goal",
            f"Turn this rough task into a clear, actionable change: {cleaned}",
            "",
            "## Scope",
            "- Clarify the smallest useful implementation path.",
            "- Keep the change focused on the requested behavior.",
            "",
            "## Non-goals",
            "- Do not include unrelated refactors.",
            "- Do not change public contracts unless the task requires it.",
            "",
            "## Implementation Notes",
            "- Prefer existing repository conventions and minimal changes.",
            "- Add or update focused verification when behavior changes.",
            "",
            "## Acceptance Criteria",
            f"- [ ] {cleaned}",
            "- [ ] Relevant local checks pass.",
        ]
    )
    return IssueDraft(title=title, body=body, labels=labels)


def suggest_labels(description: str) -> list[str]:
    normalized = description.casefold()
    labels = ["codex:ready"]
    if any(term in normalized for term in ("automation", "issue", "codex", "script", "tool", "wrapper", "autodev")):
        labels.append("automation")
    if any(term in normalized for term in ("python", "script", "tool", "automation", "wrapper")):
        labels.append("area:python")
    if any(term in normalized for term in ("windows", "linux", "cross-platform", "powershell", "bash")):
        labels.append("cross-platform")
    return _unique(labels)


def select_repository(
    description: str,
    *,
    explicit_repo: str | None,
    repo_map: dict[str, str] | None,
) -> RepoSelection:
    if explicit_repo:
        return RepoSelection(repository=explicit_repo, ambiguous=False, candidates=[])

    if not repo_map:
        return RepoSelection(repository=None, ambiguous=True, candidates=[])

    normalized = description.casefold()
    matches = {
        repository
        for keyword, repository in repo_map.items()
        if keyword.casefold() in normalized
    }

    if len(matches) == 1:
        return RepoSelection(repository=next(iter(matches)), ambiguous=False, candidates=[])

    return RepoSelection(repository=None, ambiguous=True, candidates=sorted(matches))


def run(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    gh_runner: Callable[[list[str]], str] | None = None,
    now: Callable[[], str] | None = None,
) -> int:
    output = stdout if stdout is not None else sys.stdout
    args = _parse_args(argv)

    descriptions = _load_descriptions(args)
    if not descriptions:
        print("No descriptions were provided.", file=output)
        return 2

    too_short = [description for description in descriptions if len(description.strip()) < MIN_DESCRIPTION_CHARS]
    if too_short:
        print("Refusing to create empty or near-empty issues.", file=output)
        return 2

    if args.create and len(descriptions) > args.max_issues and not args.yes:
        print(
            f"Refusing to create {len(descriptions)} issues because --max-issues is {args.max_issues}. "
            "Pass --yes to confirm.",
            file=output,
        )
        return 2

    repo_map = _load_repo_map(args.repo_map)
    log_path = Path(args.creation_log)
    seen_hashes = _read_seen_hashes(log_path)
    exit_code = 0

    for index, description in enumerate(descriptions, start=1):
        selection = select_repository(description, explicit_repo=args.repo, repo_map=repo_map)
        if selection.ambiguous or not selection.repository:
            print(f"Issue {index}: repository selection is ambiguous.", file=output)
            if selection.candidates:
                print("Candidate repositories:", file=output)
                for candidate in selection.candidates:
                    print(f"- {candidate}", file=output)
            print("Pass --repo <owner/name> to select the target repository.", file=output)
            exit_code = 2
            continue

        source_hash = _source_hash(description)
        draft = build_issue_draft(description)
        command = _build_gh_command(selection.repository, draft)

        if args.create and source_hash in seen_hashes:
            print(f"Issue {index}: Skipping duplicate description for {selection.repository}.", file=output)
            continue

        _print_proposal(
            output,
            mode="create" if args.create else "dry-run",
            repository=selection.repository,
            draft=draft,
            command=command,
        )

        if not args.create:
            continue

        if not args.yes:
            print("Refusing to create issues without --yes confirmation.", file=output)
            return 2

        runner = gh_runner or _run_gh
        created_url = runner(command).strip()
        _append_creation_log(
            log_path,
            {
                "timestamp": now() if now else datetime.now(timezone.utc).isoformat(),
                "source_description": description,
                "source_hash": source_hash,
                "selected_repository": selection.repository,
                "repository": selection.repository,
                "created_issue_url": created_url,
                "title": draft.title,
                "labels": draft.labels,
            },
        )
        seen_hashes.add(source_hash)

    return exit_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Turn rough task descriptions into structured GitHub issues."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--description", action="append", help="Raw issue idea. May be passed multiple times.")
    source.add_argument("--description-file", help="Path to a file containing one or more issue ideas.")
    parser.add_argument("--repo", help="Explicit target repository in owner/name form.")
    parser.add_argument("--repo-map", help="JSON map from keywords to owner/name repositories.")
    parser.add_argument("--dry-run", action="store_true", help="Print proposed issues without creating them.")
    parser.add_argument("--create", action="store_true", help="Create issues with gh issue create.")
    parser.add_argument("--max-issues", type=int, default=5, help="Maximum issues to create before confirmation.")
    parser.add_argument("--yes", action="store_true", help="Confirm non-interactive issue creation.")
    parser.add_argument(
        "--creation-log",
        default=str(DEFAULT_CREATION_LOG),
        help="JSONL log path for created issues.",
    )
    return parser.parse_args(argv)


def _load_descriptions(args: argparse.Namespace) -> list[str]:
    if args.description:
        return [value.strip() for value in args.description if value.strip()]

    text = Path(args.description_file).read_text(encoding="utf-8")
    return split_descriptions(text)


def _load_repo_map(path: str | None) -> dict[str, str] | None:
    if not path:
        return None
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("--repo-map must point to a JSON object.")
    return {str(key): str(repository) for key, repository in value.items()}


def _build_gh_command(repository: str, draft: IssueDraft) -> list[str]:
    command = [
        "gh",
        "issue",
        "create",
        "--repo",
        repository,
        "--title",
        draft.title,
        "--body",
        draft.body,
    ]
    for label in draft.labels:
        command.extend(["--label", label])
    return command


def _print_proposal(
    output: TextIO,
    *,
    mode: str,
    repository: str,
    draft: IssueDraft,
    command: list[str],
) -> None:
    print(f"Mode: {mode}", file=output)
    print(f"Repository: {repository}", file=output)
    print(f"Title: {draft.title}", file=output)
    print("Body:", file=output)
    print(draft.body, file=output)
    print(f"Labels: {', '.join(draft.labels)}", file=output)
    print(f"Command: {_format_command(command)}", file=output)


def _format_command(command: Iterable[str]) -> str:
    return " ".join(_quote_arg(part) for part in command)


def _quote_arg(value: str) -> str:
    if not value or any(character.isspace() for character in value) or '"' in value:
        return json.dumps(value)
    return value


def _run_gh(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def _append_creation_log(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.write("\n")


def _read_seen_hashes(path: Path) -> set[str]:
    if not path.exists():
        return set()

    hashes: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        source_hash = record.get("source_hash")
        if isinstance(source_hash, str):
            hashes.add(source_hash)
    return hashes


def _source_hash(description: str) -> str:
    normalized = " ".join(description.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


if __name__ == "__main__":
    raise SystemExit(run())

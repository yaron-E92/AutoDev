from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from automation import cli_help, opencode_entrypoint, revision


def register_help() -> None:
    cli_help.HELP.setdefault(
        ("revise",),
        cli_help.HelpEntry(
            usage=(
                "autodev revise [--repo PATH] [--runtime NAME] "
                "[--instructions TEXT | --instructions-file PATH] [--refresh-issue]"
            ),
            summary="Revise an in-progress run while preserving its current implementation.",
            description=(
                "Revision is operator-directed requirement/plan change, not verifier repair. "
                "AutoDev archives superseded synthesis/plan evidence, keeps the current implementation "
                "as revision input, invalidates dependent checkpoints, and resumes at Synthesizer -> "
                "delta Planner -> Implementer -> normal verification. `--refresh-issue` is the explicit "
                "authority boundary for adopting a changed GitHub issue; ordinary resume never does that silently."
            ),
            options=(
                ("--repo PATH", "Repository root. Default: current directory (.)."),
                ("--runtime NAME", "Role runtime override for the resumed revised workflow."),
                ("--instructions TEXT", "Authoritative inline operator revision instructions."),
                ("--instructions-file PATH", "Read authoritative operator revision instructions from a UTF-8 file."),
                ("--refresh-issue", "Explicitly adopt the current GitHub issue body before replanning."),
            ),
            examples=(
                'autodev revise --instructions "Keep the useful implementation but remove release orchestration."',
                "autodev revise --instructions-file correction.md",
                "autodev revise --refresh-issue",
                'autodev revise --refresh-issue --instructions "Preserve compatible work and update only the changed requirement."',
            ),
            privacy_note=cli_help.CLOUD_MODEL_NOTE,
        ),
    )
    cli_help.KNOWN_TOP_LEVEL.add("revise")
    groups: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for title, rows in cli_help.TOP_LEVEL_GROUPS:
        if title == "Automation and operations" and not any(
            name == "revise" for name, _ in rows
        ):
            rows = (
                ("revise", "Revise an in-progress run from corrected operator/issue authority."),
                *rows,
            )
        groups.append((title, rows))
    cli_help.TOP_LEVEL_GROUPS = tuple(groups)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodev revise")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--runtime", default="")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--instructions", default="")
    inputs.add_argument("--instructions-file", default="")
    parser.add_argument("--refresh-issue", action="store_true")
    return parser


def run_cli(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(argv)
    repo = Path(args.repo).expanduser().resolve()
    instructions_file = (
        Path(args.instructions_file).expanduser()
        if str(args.instructions_file).strip()
        else None
    )
    try:
        record = revision.begin_revision(
            repo,
            instructions=str(args.instructions or ""),
            instructions_file=instructions_file,
            refresh_issue=bool(args.refresh_issue),
        )
    except revision.RevisionError as exc:
        print(f"autodev revise: {exc}", file=stderr)
        return 2

    revision_id = str(record.get("revision_id", ""))
    print(f"Revision prepared: {revision_id}", file=stdout)
    if bool(args.refresh_issue):
        previous = str(record.get("previous_issue_sha256", ""))
        current = str(record.get("refreshed_issue_sha256", ""))
        if bool(record.get("issue_changed", False)):
            print("Issue source changed:", file=stdout)
            print(f"  previous: {previous}", file=stdout)
            print(f"  current:  {current}", file=stdout)
        else:
            print(f"Issue source unchanged: {current}", file=stdout)
    print(
        "Revision will preserve the current implementation and re-enter at synthesis.",
        file=stdout,
    )

    forwarded = ["coordinate", "--resume", "--repo", str(repo)]
    if str(args.runtime).strip():
        forwarded.extend(("--runtime", str(args.runtime).strip()))
    return opencode_entrypoint.run(forwarded)

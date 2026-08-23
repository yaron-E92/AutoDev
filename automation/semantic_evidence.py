from __future__ import annotations

import re
import subprocess
from pathlib import Path

from automation.semantic_contract import (
    ChangedFileList,
    MAX_DIFF_CHARS,
    MAX_REGRESSION_EVIDENCE_CHARS,
    MAX_REGRESSION_FILE_BYTES,
    MAX_REGRESSION_REFERENCES,
    MAX_REGRESSION_SYMBOLS,
    SEMANTIC_IGNORED_PARTS,
    SEMANTIC_SOURCE_SUFFIXES,
    SemanticVerifierError,
    _DECLARATION_PATTERNS,
)
from automation.semantic_text import (
    _bounded,
)

def collect_changed_files(repo: Path) -> list[str]:
    values = sorted(
        set(
            _git_lines(repo, ["git", "diff", "--name-only", "--relative", "--", "."])
            + _git_lines(
                repo,
                ["git", "diff", "--cached", "--name-only", "--relative", "--", "."],
            )
            + _git_lines(repo, ["git", "ls-files", "--others", "--exclude-standard"])
        )
    )
    return ChangedFileList(values, repo.expanduser().resolve())

def collect_current_diff(
    repo: Path,
    changed_files: list[str] | None = None,
) -> str:
    tracked = _git_text(
        repo,
        ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--", "."],
    )
    changed_files = changed_files if changed_files is not None else collect_changed_files(repo)
    untracked_blocks: list[str] = []
    for relative in changed_files:
        path = repo / relative
        if not path.is_file() or _is_tracked(repo, relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = "[binary or unreadable file]"
        untracked_blocks.append(
            f"diff --git a/{relative} b/{relative}\n"
            f"new file mode 100644\n--- /dev/null\n+++ b/{relative}\n"
            + "\n".join("+" + line for line in _bounded(text, 20_000).splitlines())
        )
    return _bounded(
        "\n".join(part for part in [tracked, *untracked_blocks] if part),
        MAX_DIFF_CHARS,
    )

def collect_cross_file_regression_evidence(
    repo: Path,
    changed_files: list[str] | None = None,
    diff: str | None = None,
) -> str:
    changed_files = changed_files if changed_files is not None else collect_changed_files(repo)
    diff = diff if diff is not None else collect_current_diff(repo, changed_files)
    symbols = _removed_symbol_candidates(diff)
    if not symbols:
        return "No removed/changed declaration-like identifiers were detected in the current diff."

    changed = {path.replace("\\", "/") for path in changed_files}
    references: list[str] = []
    for path in repo.rglob("*"):
        if len(references) >= MAX_REGRESSION_REFERENCES:
            break
        if not path.is_file() or path.suffix.casefold() not in SEMANTIC_SOURCE_SUFFIXES:
            continue
        relative = path.relative_to(repo).as_posix()
        if relative in changed or any(part in SEMANTIC_IGNORED_PARTS for part in Path(relative).parts):
            continue
        try:
            if path.stat().st_size > MAX_REGRESSION_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for symbol in symbols:
                if re.search(r"\b" + re.escape(symbol) + r"\b", line):
                    references.append(
                        f"- {symbol} -> {relative}:{line_number}: {_bounded(line.strip(), 240)}"
                    )
                    break
            if len(references) >= MAX_REGRESSION_REFERENCES:
                break

    lines = ["Removed/changed declaration candidates:"]
    lines.extend(f"- {symbol}" for symbol in symbols)
    lines.append("")
    lines.append("References in unchanged source files:")
    if references:
        lines.extend(references)
    else:
        lines.append("- No unchanged-file references to the bounded candidates were found.")
    lines.append("")
    lines.append(
        "Verifier instruction: treat a removed/changed symbol that is still referenced by unchanged code as a potential blocking regression unless deterministic evidence proves the reference remains valid."
    )
    return _bounded("\n".join(lines), MAX_REGRESSION_EVIDENCE_CHARS)

def collect_deterministic_evidence(current_dir: Path) -> str:
    parts: list[str] = []
    for name in (
        "verification-result-summary.md",
        "local-check.log",
        "recommended-command-groups.json",
        "ci-summary.json",
    ):
        path = current_dir / name
        if path.is_file():
            parts.append(
                f"## {name}\n{_bounded(path.read_text(encoding='utf-8'), 12_000)}"
            )
    return "\n\n".join(parts) or "No deterministic evidence artifact was available."

def _removed_symbol_candidates(diff: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for raw in diff.splitlines():
        if not raw.startswith("-") or raw.startswith("---"):
            continue
        line = raw[1:].strip()
        for pattern in _DECLARATION_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            symbol = match.group(1)
            if len(symbol) < 3 or symbol in seen:
                continue
            seen.add(symbol)
            candidates.append(symbol)
            if len(candidates) >= MAX_REGRESSION_SYMBOLS:
                return candidates
    return candidates

def _git_lines(repo: Path, argv: list[str]) -> list[str]:
    return [line.strip() for line in _git_text(repo, argv).splitlines() if line.strip()]

def _git_text(repo: Path, argv: list[str]) -> str:
    completed = subprocess.run(
        argv,
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        evidence = (completed.stderr or completed.stdout or "").strip()
        raise SemanticVerifierError(
            f"semantic evidence command failed ({completed.returncode}): {' '.join(argv)}: "
            f"{_bounded(evidence, 1000)}",
            classification="evidence_collection_failed",
        )
    return completed.stdout or ""

def _is_tracked(repo: Path, relative: str) -> bool:
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


INTENT_RE = re.compile(r"(?im)^\s*\+semver:\s*(major|minor|patch|none)\s*$")
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
BUMP_RANK = {"none": 0, "patch": 1, "minor": 2, "major": 3}


class VersionPolicyError(RuntimeError):
    pass


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse_tag(cls, tag: str) -> "Version":
        match = TAG_RE.fullmatch(tag.strip())
        if not match:
            raise VersionPolicyError(f"not a canonical version tag: {tag!r}")
        return cls(*(int(value) for value in match.groups()))

    def bump(self, intent: str) -> "Version":
        if intent == "none":
            return self
        if intent == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        if intent == "minor":
            return Version(self.major, self.minor + 1, 0)
        if intent == "major":
            return Version(self.major + 1, 0, 0)
        raise VersionPolicyError(f"unsupported semver intent: {intent!r}")

    @property
    def semver(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @property
    def tag(self) -> str:
        return f"v{self.semver}"


@dataclass(frozen=True)
class Resolution:
    base_tag: str
    base_version: Version
    bump: str
    version: Version
    source_sha: str
    intents: tuple[str, ...]
    superseded: bool = False

    @property
    def tag_required(self) -> bool:
        return not self.superseded and self.bump != "none"

    def as_dict(self) -> dict[str, object]:
        return {
            "base_tag": self.base_tag,
            "base_version": self.base_version.semver,
            "bump": self.bump,
            "version": self.version.semver,
            "tag": self.version.tag,
            "source_sha": self.source_sha,
            "tag_required": self.tag_required,
            "superseded": self.superseded,
            "intents": list(self.intents),
        }


Runner = Callable[..., subprocess.CompletedProcess[str]]


def parse_exact_intent(text: str) -> str:
    matches = INTENT_RE.findall(text or "")
    if len(matches) != 1:
        if not matches:
            raise VersionPolicyError(
                "exactly one version intent is required: add one line containing "
                "+semver: major|minor|patch|none"
            )
        raise VersionPolicyError(
            "exactly one version intent is required; duplicate or conflicting +semver directives are not allowed"
        )
    return matches[0].casefold()


def explicit_intents(text: str) -> list[str]:
    return [value.casefold() for value in INTENT_RE.findall(text or "")]


def highest_bump(intents: Iterable[str]) -> str:
    values = [value.casefold() for value in intents]
    if not values:
        return "none"
    unknown = [value for value in values if value not in BUMP_RANK]
    if unknown:
        raise VersionPolicyError(f"unsupported semver intent(s): {', '.join(sorted(set(unknown)))}")
    return max(values, key=lambda value: BUMP_RANK[value])


def latest_reachable_tag(repo: Path, head: str = "HEAD", *, runner: Runner = subprocess.run) -> tuple[str, Version]:
    completed = _run(
        runner,
        ["git", "tag", "--merged", head, "--list", "v*", "--sort=-v:refname"],
        cwd=repo,
    )
    for raw in completed.stdout.splitlines():
        tag = raw.strip()
        if TAG_RE.fullmatch(tag):
            return tag, Version.parse_tag(tag)
    return "", Version(0, 0, 0)


def candidate_for_pr(repo: Path, body: str, *, head: str = "HEAD", runner: Runner = subprocess.run) -> Resolution:
    intent = parse_exact_intent(body)
    base_tag, base = latest_reachable_tag(repo, head, runner=runner)
    version = base.bump(intent)
    return Resolution(
        base_tag=base_tag,
        base_version=base,
        bump=intent,
        version=version,
        source_sha=_git(repo, ["rev-parse", head], runner=runner).strip(),
        intents=(intent,),
    )


def resolve_main(
    repo: Path,
    *,
    repository: str,
    head: str,
    branch: str = "main",
    runner: Runner = subprocess.run,
) -> Resolution:
    repo = repo.expanduser().resolve()
    _run(runner, ["git", "fetch", "origin", branch, "--tags", "--force"], cwd=repo)
    remote_head = _git(repo, ["rev-parse", f"origin/{branch}"], runner=runner).strip()
    source_sha = _git(repo, ["rev-parse", head], runner=runner).strip()

    if remote_head != source_sha:
        base_tag, base = latest_reachable_tag(repo, source_sha, runner=runner)
        return Resolution(
            base_tag=base_tag,
            base_version=base,
            bump="none",
            version=base,
            source_sha=source_sha,
            intents=(),
            superseded=True,
        )

    base_tag, base = latest_reachable_tag(repo, source_sha, runner=runner)
    revision_range = f"{base_tag}..{source_sha}" if base_tag else source_sha
    commits = _git(repo, ["rev-list", "--reverse", revision_range], runner=runner).splitlines()

    intents: list[str] = []
    seen_prs: set[int] = set()
    for commit in (value.strip() for value in commits if value.strip()):
        pulls = _associated_pulls(repository, commit, runner=runner, cwd=repo)
        merged = [
            item
            for item in pulls
            if isinstance(item, dict)
            and item.get("merged_at")
            and isinstance(item.get("base"), dict)
            and str(item["base"].get("ref", "")) == branch
        ]
        if merged:
            for pull in merged:
                number = int(pull.get("number", 0) or 0)
                if number <= 0 or number in seen_prs:
                    continue
                seen_prs.add(number)
                values = explicit_intents(str(pull.get("body", "") or ""))
                if len(values) > 1:
                    raise VersionPolicyError(
                        f"merged PR #{number} contains duplicate/conflicting +semver directives"
                    )
                if values:
                    intents.extend(values)
            continue

        # Direct commits are uncommon on protected main branches. Preserve an
        # explicit directive when present, but do not invent a patch bump for
        # legacy/unannotated history.
        message = _git(repo, ["show", "-s", "--format=%B", commit], runner=runner)
        values = explicit_intents(message)
        if len(values) > 1:
            raise VersionPolicyError(
                f"direct main commit {commit[:12]} contains duplicate/conflicting +semver directives"
            )
        if values:
            intents.extend(values)

    bump = highest_bump(intents)
    version = base.bump(bump)
    return Resolution(
        base_tag=base_tag,
        base_version=base,
        bump=bump,
        version=version,
        source_sha=source_sha,
        intents=tuple(intents),
    )


def create_annotated_tag(
    repo: Path,
    resolution: Resolution,
    *,
    runner: Runner = subprocess.run,
) -> str:
    if resolution.superseded:
        return "superseded"
    if not resolution.tag_required:
        return "no-tag"

    tag = resolution.version.tag
    source_sha = resolution.source_sha
    existing = _run(
        runner,
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
        cwd=repo,
        check=False,
    )
    if existing.returncode == 0:
        existing_sha = existing.stdout.strip()
        if existing_sha != source_sha:
            raise VersionPolicyError(
                f"refusing to move existing tag {tag}: it points to {existing_sha}, not {source_sha}"
            )
        return "already-exists"

    _run(
        runner,
        ["git", "tag", "-a", tag, source_sha, "-m", f"Version {resolution.version.semver}"],
        cwd=repo,
    )
    pushed = _run(
        runner,
        ["git", "push", "origin", f"refs/tags/{tag}"],
        cwd=repo,
        check=False,
    )
    if pushed.returncode != 0:
        # A concurrent allocator may have won. Fetch and accept only an exact
        # same-SHA tag; otherwise the collision is a hard failure.
        _run(runner, ["git", "fetch", "origin", "--tags", "--force"], cwd=repo)
        remote = _run(
            runner,
            ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
            cwd=repo,
            check=False,
        )
        if remote.returncode == 0 and remote.stdout.strip() == source_sha:
            return "concurrent-identical"
        raise VersionPolicyError(
            f"failed to push version tag {tag}: {pushed.stderr.strip() or pushed.stdout.strip()}"
        )
    return "created"


def _associated_pulls(repository: str, commit: str, *, runner: Runner, cwd: Path) -> list[object]:
    completed = _run(
        runner,
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            f"repos/{repository}/commits/{commit}/pulls",
        ],
        cwd=cwd,
    )
    try:
        value = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise VersionPolicyError(
            f"GitHub returned invalid PR association JSON for commit {commit[:12]}"
        ) from exc
    if not isinstance(value, list):
        raise VersionPolicyError(
            f"GitHub returned an unexpected PR association payload for commit {commit[:12]}"
        )
    return value


def _git(repo: Path, arguments: list[str], *, runner: Runner) -> str:
    return _run(runner, ["git", *arguments], cwd=repo).stdout


def _run(
    runner: Runner,
    command: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = runner(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and int(getattr(completed, "returncode", 1)) != 0:
        stderr = str(getattr(completed, "stderr", "") or "").strip()
        stdout = str(getattr(completed, "stdout", "") or "").strip()
        raise VersionPolicyError(
            f"command failed ({' '.join(command)}): {stderr or stdout or 'no output'}"
        )
    return completed


def write_github_outputs(path: str, values: dict[str, object]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, list):
                rendered = json.dumps(value, separators=(",", ":"))
            else:
                rendered = str(value)
            stream.write(f"{key}={rendered}\n")


def _summary(resolution: Resolution) -> str:
    payload = resolution.as_dict()
    return (
        f"base={payload['base_tag'] or '(none)'} bump={payload['bump']} "
        f"version={payload['version']} tag={payload['tag']} "
        f"required={payload['tag_required']} superseded={payload['superseded']}"
    )


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m automation.version_policy")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pr = subparsers.add_parser("check-pr")
    pr.add_argument("--repo", default=".")
    pr.add_argument("--body-env", default="PR_BODY")
    pr.add_argument("--head", default="HEAD")
    pr.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))

    main = subparsers.add_parser("resolve-main")
    main.add_argument("--repo", default=".")
    main.add_argument("--repository", required=True)
    main.add_argument("--head", required=True)
    main.add_argument("--branch", default="main")
    main.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    main.add_argument("--create-tag", action="store_true")

    args = parser.parse_args(argv)
    try:
        repo = Path(args.repo).expanduser().resolve()
        if args.command == "check-pr":
            resolution = candidate_for_pr(
                repo,
                os.environ.get(args.body_env, ""),
                head=args.head,
            )
            write_github_outputs(args.github_output, resolution.as_dict())
            print(_summary(resolution))
            return 0

        resolution = resolve_main(
            repo,
            repository=args.repository,
            head=args.head,
            branch=args.branch,
        )
        values = resolution.as_dict()
        if args.create_tag:
            values["tag_status"] = create_annotated_tag(repo, resolution)
        write_github_outputs(args.github_output, values)
        print(_summary(resolution))
        if args.create_tag:
            print(f"tag_status={values['tag_status']}")
        return 0
    except VersionPolicyError as exc:
        print(f"Version policy error: {exc}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())

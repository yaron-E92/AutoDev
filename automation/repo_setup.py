from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from automation import (
    execution_classification_hooks,
    issue_queue,
    opencode_adapter,
    opencode_install,
    privacy,
    privacy_grants,
    queue_selection,
)


REPO_CONFIG = Path(".autodev") / "repo.json"
QUEUE_CONFIG = issue_queue.QUEUE_CONFIG
ROADMAP_CONFIG = queue_selection.ROADMAP_PATH
PRIVACY_CONFIG = privacy.PRIVACY_CONFIG
LEGACY_OPENCODE_CONFIG = Path(".opencode") / "autodev.json"
REPO_SCHEMA = 1
ROADMAP_TEMPLATE = "version: 1\npriority: []\nfallback: oldest\n"
DEFAULT_QUEUE = {"version": 1, "autonomous_execution": True}
DEFAULT_PRIVACY = {"profile": "strict-confidential", "consent_mode": "explicit"}


class RepoSetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class RepoInstallResult:
    repository: str
    github_repository: str
    created: tuple[str, ...]
    updated: tuple[str, ...]
    removed_legacy: tuple[str, ...]
    labels_created: tuple[str, ...]
    opencode_enabled: bool

    def to_json(self) -> dict[str, object]:
        value = asdict(self)
        for key in ("created", "updated", "removed_legacy", "labels_created"):
            value[key] = list(value[key])
        return value


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    state: str
    detail: str
    fixable: bool = False

    @property
    def ok(self) -> bool:
        return self.state in {"ok", "info"}


@dataclass(frozen=True)
class DoctorResult:
    repository: str
    checks: tuple[DoctorCheck, ...]
    fixed: bool = False

    @property
    def healthy(self) -> bool:
        return all(item.ok for item in self.checks)

    def to_json(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "healthy": self.healthy,
            "fixed": self.fixed,
            "checks": [asdict(item) for item in self.checks],
        }


def _repo(repo: Path) -> Path:
    value = repo.expanduser().resolve()
    if not value.is_dir():
        raise RepoSetupError(f"repository directory does not exist: {value}")
    if not (value / ".git").exists():
        raise RepoSetupError(f"not a Git repository root: {value}")
    return value


def _write_if_missing(path: Path, content: str, created: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(path.as_posix())


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _load_repo_config(repo: Path) -> dict[str, object]:
    path = repo / REPO_CONFIG
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RepoSetupError(f"invalid AutoDev repository config: {path}") from exc
    if not isinstance(value, dict):
        raise RepoSetupError(f"AutoDev repository config must be a JSON object: {path}")
    if value.get("version") != REPO_SCHEMA:
        raise RepoSetupError(
            f"unsupported AutoDev repository config version in {path}: {value.get('version')!r}"
        )
    return value


def _ensure_repo_config(repo: Path, *, opencode_enabled: bool, created: list[str], updated: list[str]) -> None:
    path = repo / REPO_CONFIG
    current = _load_repo_config(repo)
    if not current:
        value = {"version": REPO_SCHEMA, "opencode": {"enabled": opencode_enabled}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json_text(value), encoding="utf-8")
        created.append(path.as_posix())
        return
    value = dict(current)
    raw_opencode = value.get("opencode", {})
    opencode = dict(raw_opencode) if isinstance(raw_opencode, dict) else {}
    if opencode.get("enabled") is not opencode_enabled:
        opencode["enabled"] = opencode_enabled
        value["opencode"] = opencode
        path.write_text(_json_text(value), encoding="utf-8")
        updated.append(path.as_posix())


def opencode_enabled(repo: Path) -> bool:
    config = _load_repo_config(repo)
    if not config:
        return True
    raw = config.get("opencode", {})
    return bool(raw.get("enabled", True)) if isinstance(raw, dict) else True


def _legacy_config_is_autodev_owned(path: Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    return set(value).issubset({"version", "autodev_root", "python"}) and bool(
        value.get("autodev_root") and value.get("python")
    )


def _remove_legacy_config(repo: Path, removed: list[str]) -> None:
    path = repo / LEGACY_OPENCODE_CONFIG
    if not path.is_file():
        return
    if not _legacy_config_is_autodev_owned(path):
        raise RepoSetupError(
            f"legacy path {path} exists but is not a recognized AutoDev-owned config; refusing to delete it"
        )
    path.unlink()
    removed.append(path.as_posix())


def _resolve_github_repo(
    repo: Path,
    explicit: str,
    *,
    runner: Callable[..., object],
) -> str:
    return issue_queue.resolve_github_repo(repo, explicit=explicit, runner=runner)


def install_repo(
    repo: Path,
    *,
    github_repo: str = "",
    enable_opencode: bool = True,
    autodev_root: Path | None = None,
    python_command: str = sys.executable,
    runner: Callable[..., object] = subprocess.run,
) -> RepoInstallResult:
    repo = _repo(repo)
    root = (autodev_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    created: list[str] = []
    updated: list[str] = []
    removed: list[str] = []

    _ensure_repo_config(
        repo,
        opencode_enabled=enable_opencode,
        created=created,
        updated=updated,
    )
    _write_if_missing(repo / QUEUE_CONFIG, _json_text(DEFAULT_QUEUE), created)
    _write_if_missing(repo / ROADMAP_CONFIG, ROADMAP_TEMPLATE, created)
    _write_if_missing(repo / PRIVACY_CONFIG, _json_text(DEFAULT_PRIVACY), created)

    # Validate repository-owned policy before mutating GitHub or frontend assets.
    issue_queue.load_policy(repo)
    queue_selection.load_roadmap(repo)
    privacy.load_policy(repo)

    if enable_opencode:
        before = {
            path.relative_to(repo).as_posix()
            for path in repo.glob(".opencode/**/*")
            if path.is_file()
        }
        installed = opencode_install.install_assets(
            repo,
            root,
            python_command=python_command,
        )
        for path in installed:
            relative = path.relative_to(repo).as_posix()
            (updated if relative in before else created).append(relative)
        _remove_legacy_config(repo, removed)

    execution_classification_hooks.install()
    resolved = _resolve_github_repo(repo, github_repo, runner=runner)
    labels = issue_queue.ensure_queue_labels(repo, resolved, runner=runner)
    return RepoInstallResult(
        repository=str(repo),
        github_repository=resolved,
        created=tuple(sorted(set(created))),
        updated=tuple(sorted(set(updated))),
        removed_legacy=tuple(sorted(set(removed))),
        labels_created=tuple(sorted(labels)),
        opencode_enabled=enable_opencode,
    )


def ensure_labels(
    repo: Path,
    *,
    github_repo: str = "",
    runner: Callable[..., object] = subprocess.run,
) -> tuple[str, ...]:
    repo = _repo(repo)
    execution_classification_hooks.install()
    resolved = _resolve_github_repo(repo, github_repo, runner=runner)
    return issue_queue.ensure_queue_labels(repo, resolved, runner=runner)


def _label_check(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object],
) -> DoctorCheck:
    result = issue_queue._run_gh(  # type: ignore[attr-defined]
        repo,
        [
            "label",
            "list",
            "--repo",
            github_repo,
            "--limit",
            "1000",
            "--json",
            "name,color,description",
        ],
        runner=runner,
        check=False,
    )
    if result.returncode != 0:
        return DoctorCheck("queue-labels", "error", "cannot read GitHub labels", True)
    raw = issue_queue._json_result(result, context="gh label list")  # type: ignore[attr-defined]
    actual: dict[str, tuple[str, str]] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                actual[str(item["name"])] = (
                    str(item.get("color", "")).casefold(),
                    str(item.get("description", "")),
                )
    drift = [
        name
        for name, (color, description) in issue_queue.LABEL_SPECS.items()
        if actual.get(name) != (color.casefold(), description)
    ]
    if drift:
        return DoctorCheck(
            "queue-labels",
            "error",
            "missing or non-canonical labels: " + ", ".join(drift),
            True,
        )
    return DoctorCheck("queue-labels", "ok", "canonical queue labels are present")


def _grant_check(repo: Path) -> DoctorCheck:
    try:
        summary = privacy_grants.grant_summary(repo)
    except Exception as exc:  # defensive: doctor must not expose or corrupt grant state
        return DoctorCheck("privacy-grants", "error", f"cannot inspect grant metadata: {exc}")
    return DoctorCheck(
        "privacy-grants",
        "info",
        f"active={summary['active']} expired={summary['expired']} revoked={summary['revoked']}",
    )


def doctor(
    repo: Path,
    *,
    fix: bool = False,
    github_repo: str = "",
    autodev_root: Path | None = None,
    python_command: str = sys.executable,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> DoctorResult:
    repo = _repo(repo)
    fixed = False
    if fix:
        desired_opencode = opencode_enabled(repo) if (repo / REPO_CONFIG).is_file() else True
        install_repo(
            repo,
            github_repo=github_repo,
            enable_opencode=desired_opencode,
            autodev_root=autodev_root,
            python_command=python_command,
            runner=runner,
        )
        fixed = True

    checks: list[DoctorCheck] = []
    root = (autodev_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    checks.append(
        DoctorCheck(
            "cli",
            "ok" if (root / "automation" / "autodev_cli.py").is_file() else "error",
            f"canonical CLI root: {root}",
        )
    )
    for command in ("git", "gh"):
        resolved = which(command)
        checks.append(
            DoctorCheck(
                f"tool:{command}",
                "ok" if resolved else "error",
                resolved or f"{command} is not on PATH",
            )
        )

    try:
        config = _load_repo_config(repo)
        if not config:
            checks.append(DoctorCheck("repo-config", "error", f"missing {REPO_CONFIG}", True))
            open_code = True
        else:
            checks.append(DoctorCheck("repo-config", "ok", f"{REPO_CONFIG} version {REPO_SCHEMA}"))
            open_code = opencode_enabled(repo)
    except RepoSetupError as exc:
        checks.append(DoctorCheck("repo-config", "error", str(exc), True))
        open_code = True

    try:
        issue_queue.load_policy(repo)
        checks.append(DoctorCheck("queue-policy", "ok", str(QUEUE_CONFIG)))
    except Exception as exc:
        checks.append(DoctorCheck("queue-policy", "error", str(exc), True))
    try:
        roadmap = queue_selection.load_roadmap(repo)
        checks.append(
            DoctorCheck(
                "roadmap",
                "ok" if (repo / ROADMAP_CONFIG).is_file() else "info",
                roadmap.path or "no roadmap; deterministic oldest fallback applies",
                not (repo / ROADMAP_CONFIG).is_file(),
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("roadmap", "error", str(exc), True))
    try:
        policy = privacy.load_policy(repo)
        checks.append(
            DoctorCheck(
                "privacy-policy",
                "ok",
                f"profile={policy.profile} consent_mode={policy.consent_mode}",
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("privacy-policy", "error", str(exc), True))
    checks.append(_grant_check(repo))

    legacy = repo / LEGACY_OPENCODE_CONFIG
    checks.append(
        DoctorCheck(
            "legacy-opencode-config",
            "error" if legacy.exists() else "ok",
            f"legacy generic AutoDev config remains at {legacy}" if legacy.exists() else "generic AutoDev config is not stored under .opencode",
            legacy.exists(),
        )
    )

    resolved_repo = ""
    try:
        resolved_repo = _resolve_github_repo(repo, github_repo, runner=runner)
        checks.append(DoctorCheck("github-repository", "ok", resolved_repo))
        checks.append(_label_check(repo, resolved_repo, runner=runner))
    except Exception as exc:
        checks.append(DoctorCheck("github-repository", "error", str(exc)))

    if open_code:
        missing = [
            f".opencode/commands/{name}"
            for name in opencode_adapter.COMMAND_FILES
            if not (repo / ".opencode" / "commands" / name).is_file()
        ] + [
            f".opencode/agents/{name}"
            for name in opencode_adapter.AGENT_FILES
            if not (repo / ".opencode" / "agents" / name).is_file()
        ]
        if missing:
            checks.append(
                DoctorCheck(
                    "opencode-assets",
                    "error",
                    "missing AutoDev OpenCode assets: " + ", ".join(missing[:6]),
                    True,
                )
            )
        else:
            checks.append(DoctorCheck("opencode-assets", "ok", "OpenCode commands/agents are installed"))
        opencode_cli = which("opencode")
        if not opencode_cli:
            checks.append(DoctorCheck("opencode-models", "error", "OpenCode is enabled but `opencode` is not on PATH"))
        else:
            try:
                mappings = opencode_adapter.resolve_opencode_model_mappings(
                    repo,
                    runner=runner,
                    which=lambda _name: opencode_cli,
                )
                checks.append(
                    DoctorCheck(
                        "opencode-models",
                        "ok",
                        f"effective role mappings resolved for {len(mappings)} AutoDev roles; opencode.json(c) remains authoritative",
                    )
                )
            except Exception as exc:
                checks.append(DoctorCheck("opencode-models", "error", str(exc)))
    else:
        checks.append(DoctorCheck("opencode-assets", "info", "OpenCode integration disabled by repository config"))

    checks.append(
        DoctorCheck(
            "scheduler",
            "info",
            "no scheduler registration expected until scheduler support is installed",
        )
    )
    return DoctorResult(str(repo), tuple(checks), fixed=fixed)


def _render_doctor(result: DoctorResult) -> str:
    lines = [f"AutoDev doctor for {result.repository}"]
    for item in result.checks:
        marker = {"ok": "OK", "info": "INFO", "error": "ERROR"}.get(item.state, item.state.upper())
        suffix = " [fixable]" if item.fixable else ""
        lines.append(f"{marker:5} {item.name}: {item.detail}{suffix}")
    lines.append("HEALTHY" if result.healthy else "NEEDS ATTENTION")
    return "\n".join(lines)


def run_cli(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., object] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    parser = argparse.ArgumentParser(prog="autodev repo")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install")
    install.add_argument("--repo", default=".")
    install.add_argument("--github-repo", default="")
    install.add_argument("--no-opencode", action="store_true")
    install.add_argument("--python", default=sys.executable)
    install.add_argument("--json", action="store_true")

    labels = sub.add_parser("ensure-labels")
    labels.add_argument("--repo", default=".")
    labels.add_argument("--github-repo", default="")
    labels.add_argument("--json", action="store_true")

    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--repo", default=".")
    doctor_parser.add_argument("--github-repo", default="")
    doctor_parser.add_argument("--fix", action="store_true")
    doctor_parser.add_argument("--python", default=sys.executable)
    doctor_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install_repo(
                Path(args.repo),
                github_repo=args.github_repo,
                enable_opencode=not args.no_opencode,
                python_command=args.python,
                runner=runner,
            )
            print(json.dumps(result.to_json(), sort_keys=True) if args.json else f"Installed AutoDev repository assets in {result.repository}; GitHub labels ready for {result.github_repository}.")
            return 0
        if args.command == "ensure-labels":
            created = ensure_labels(
                Path(args.repo),
                github_repo=args.github_repo,
                runner=runner,
            )
            payload = {"created": list(created), "state": "OK"}
            print(json.dumps(payload, sort_keys=True) if args.json else f"Queue labels ensured; created={len(created)}.")
            return 0
        result = doctor(
            Path(args.repo),
            fix=bool(args.fix),
            github_repo=args.github_repo,
            python_command=args.python,
            runner=runner,
            which=which,
        )
        print(json.dumps(result.to_json(), sort_keys=True) if args.json else _render_doctor(result))
        return 0 if result.healthy else 2
    except (RepoSetupError, issue_queue.QueueError, privacy.PrivacyError, queue_selection.RoadmapError, opencode_adapter.OpenCodeAdapterError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())

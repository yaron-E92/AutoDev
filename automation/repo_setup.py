from __future__ import annotations

from automation import privacy_grant_commands, queue_contract, queue_github, queue_policy, semver_intent

from automation import opencode_adapter_models

from automation import opencode_adapter_contract

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from automation import execution_classification_hooks, opencode_install, privacy, queue_selection


REPO_CONFIG = Path(".autodev") / "repo.json"
QUEUE_CONFIG = queue_contract.QUEUE_CONFIG
ROADMAP_CONFIG = queue_selection.ROADMAP_PATH
PRIVACY_CONFIG = privacy.PRIVACY_CONFIG
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
    labels_created: tuple[str, ...]
    opencode_enabled: bool

    def to_json(self) -> dict[str, object]:
        value = asdict(self)
        for key in ("created", "updated", "labels_created"):
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


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _write_if_missing(path: Path, content: str, created: list[str]) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(path.relative_to(path.parents[1]).as_posix() if len(path.parents) > 1 else path.as_posix())


def _record_relative(repo: Path, path: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


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
    if "default_semver_intent" in value:
        raw_semver = value.get("default_semver_intent")
        if not isinstance(raw_semver, str):
            raise RepoSetupError(
                f"default_semver_intent in {path} must be a string"
            )
        try:
            semver_intent.normalize_intent(raw_semver, source="repository-default")
        except semver_intent.SemVerIntentError as exc:
            raise RepoSetupError(str(exc)) from exc
    return value


def _ensure_repo_config(
    repo: Path,
    *,
    enable_opencode: bool,
    created: list[str],
    updated: list[str],
) -> None:
    path = repo / REPO_CONFIG
    current = _load_repo_config(repo)
    if not current:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json_text(
                {
                    "version": REPO_SCHEMA,
                    "opencode": {"enabled": enable_opencode},
                    "default_semver_intent": semver_intent.DEFAULT_INTENT,
                }
            ),
            encoding="utf-8",
        )
        created.append(REPO_CONFIG.as_posix())
        return

    value = dict(current)
    changed = False
    if "default_semver_intent" not in value:
        value["default_semver_intent"] = semver_intent.DEFAULT_INTENT
        changed = True
    raw = value.get("opencode", {})
    opencode = dict(raw) if isinstance(raw, dict) else {}
    if opencode.get("enabled") is not enable_opencode:
        opencode["enabled"] = enable_opencode
        value["opencode"] = opencode
        changed = True
    if not changed:
        return
    path.write_text(_json_text(value), encoding="utf-8")
    updated.append(REPO_CONFIG.as_posix())


def opencode_enabled(repo: Path) -> bool:
    config = _load_repo_config(repo)
    if not config:
        return True
    raw = config.get("opencode", {})
    if not isinstance(raw, dict):
        return True
    value = raw.get("enabled", True)
    return bool(value) if isinstance(value, bool) else True



def _validate_repo_policy(repo: Path) -> None:
    queue_policy.load_policy(repo)
    queue_selection.load_roadmap(repo)
    privacy.load_policy(repo)


def _resolve_github_repo(
    repo: Path,
    explicit: str,
    *,
    runner: Callable[..., object],
) -> str:
    return queue_github.resolve_github_repo(repo, explicit=explicit, runner=runner)


def install_repo(
    repo: Path,
    *,
    github_repo: str = "",
    enable_opencode: bool = True,
    autodev_root: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> RepoInstallResult:
    repo = _repo(repo)
    root = (autodev_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    created: list[str] = []
    updated: list[str] = []

    _ensure_repo_config(
        repo,
        enable_opencode=enable_opencode,
        created=created,
        updated=updated,
    )
    queue_path = repo / QUEUE_CONFIG
    roadmap_path = repo / ROADMAP_CONFIG
    privacy_path = repo / PRIVACY_CONFIG
    if not queue_path.exists():
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        queue_path.write_text(_json_text(DEFAULT_QUEUE), encoding="utf-8")
        created.append(QUEUE_CONFIG.as_posix())
    if not roadmap_path.exists():
        roadmap_path.parent.mkdir(parents=True, exist_ok=True)
        roadmap_path.write_text(ROADMAP_TEMPLATE, encoding="utf-8")
        created.append(ROADMAP_CONFIG.as_posix())
    if not privacy_path.exists():
        privacy_path.parent.mkdir(parents=True, exist_ok=True)
        privacy_path.write_text(_json_text(DEFAULT_PRIVACY), encoding="utf-8")
        created.append(PRIVACY_CONFIG.as_posix())

    # Validate all user/repository-owned policy before GitHub mutations. Existing
    # malformed policy is never silently overwritten by install/doctor --fix.
    _validate_repo_policy(repo)

    if enable_opencode:
        before = {
            path.relative_to(repo).as_posix()
            for path in repo.glob(".opencode/**/*")
            if path.is_file()
        }
        installed = opencode_install.install_assets(
            repo,
            root,
        )
        for path in installed:
            relative = _record_relative(repo, path)
            (updated if relative in before else created).append(relative)

    execution_classification_hooks.install()
    resolved = _resolve_github_repo(repo, github_repo, runner=runner)
    labels = queue_github.ensure_queue_labels(repo, resolved, runner=runner)
    return RepoInstallResult(
        repository=str(repo),
        github_repository=resolved,
        created=tuple(sorted(set(created))),
        updated=tuple(sorted(set(updated))),
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
    return queue_github.ensure_queue_labels(repo, resolved, runner=runner)


def _label_check(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object],
) -> DoctorCheck:
    result = queue_github._run_gh(  # type: ignore[attr-defined]
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
        return DoctorCheck("queue-labels", "error", "cannot read GitHub labels")
    raw = queue_github._json_result(result, context="gh label list")  # type: ignore[attr-defined]
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
        for name, (color, description) in queue_contract.LABEL_SPECS.items()
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
        grants = privacy_grant_commands.current_grants(repo)
    except Exception as exc:  # defensive: never turn doctor into grant mutation
        return DoctorCheck("privacy-grants", "error", f"cannot inspect grant metadata: {exc}")
    counts = {"active": 0, "expired": 0, "revoked": 0}
    for record in grants:
        state = str(record.get("status", ""))
        if state in counts:
            counts[state] += 1
    return DoctorCheck(
        "privacy-grants",
        "info",
        f"active={counts['active']} expired={counts['expired']} revoked={counts['revoked']}",
    )


def _check_repo_config(repo: Path) -> tuple[DoctorCheck, bool]:
    try:
        config = _load_repo_config(repo)
    except RepoSetupError as exc:
        return DoctorCheck("repo-config", "error", str(exc), True), True
    if not config:
        return DoctorCheck("repo-config", "error", f"missing {REPO_CONFIG}", True), True
    return DoctorCheck("repo-config", "ok", f"{REPO_CONFIG} version {REPO_SCHEMA}"), opencode_enabled(repo)


def _check_policy(repo: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    try:
        queue_policy.load_policy(repo)
        checks.append(
            DoctorCheck(
                "queue-policy",
                "ok" if (repo / QUEUE_CONFIG).is_file() else "error",
                str(QUEUE_CONFIG) if (repo / QUEUE_CONFIG).is_file() else f"missing {QUEUE_CONFIG}",
                not (repo / QUEUE_CONFIG).is_file(),
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("queue-policy", "error", str(exc), True))

    try:
        roadmap = queue_selection.load_roadmap(repo)
        present = (repo / ROADMAP_CONFIG).is_file()
        checks.append(
            DoctorCheck(
                "roadmap",
                "ok" if present else "info",
                roadmap.path or "no roadmap; deterministic oldest fallback applies",
                not present,
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("roadmap", "error", str(exc), False))

    try:
        policy = privacy.load_policy(repo)
        present = (repo / PRIVACY_CONFIG).is_file()
        checks.append(
            DoctorCheck(
                "privacy-policy",
                "ok" if present else "info",
                f"profile={policy.profile} consent_mode={policy.consent_mode} source={policy.source}",
                not present,
            )
        )
    except Exception as exc:
        checks.append(DoctorCheck("privacy-policy", "error", str(exc), False))
    return checks


def _check_opencode(
    repo: Path,
    *,
    enabled: bool,
    runner: Callable[..., object],
    which: Callable[[str], str | None],
) -> list[DoctorCheck]:
    if not enabled:
        return [
            DoctorCheck(
                "opencode-assets",
                "info",
                "OpenCode integration disabled by .autodev/repo.json",
            )
        ]

    checks: list[DoctorCheck] = []
    missing = [
        f".opencode/commands/{name}"
        for name in opencode_adapter_contract.COMMAND_FILES
        if not (repo / ".opencode" / "commands" / name).is_file()
    ] + [
        f".opencode/agents/{name}"
        for name in opencode_adapter_contract.AGENT_FILES
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
        checks.append(
            DoctorCheck(
                "opencode-assets",
                "ok",
                "OpenCode commands/agents use the canonical AutoDev launcher contract",
            )
        )

    opencode_cli = which("opencode")
    if not opencode_cli:
        checks.append(
            DoctorCheck(
                "opencode-models",
                "error",
                "OpenCode is enabled but `opencode` is not on PATH",
            )
        )
        return checks
    try:
        mappings = opencode_adapter_models.resolve_opencode_model_mappings(
            repo,
            runner=runner,
            which=lambda _command: opencode_cli,
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
    return checks


def doctor(
    repo: Path,
    *,
    fix: bool = False,
    github_repo: str = "",
    autodev_root: Path | None = None,
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
            runner=runner,
        )
        fixed = True

    root = (autodev_root or Path(__file__).resolve().parents[1]).expanduser().resolve()
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).expanduser().resolve()
        cli_check = DoctorCheck(
            "cli",
            "ok" if executable.is_file() else "error",
            f"native CLI executable: {executable}",
        )
    else:
        cli_check = DoctorCheck(
            "cli",
            "ok" if (root / "automation" / "autodev_cli.py").is_file() else "error",
            f"canonical CLI root: {root}",
        )
    checks: list[DoctorCheck] = [cli_check]
    for command in ("git", "gh"):
        resolved = which(command)
        checks.append(
            DoctorCheck(
                f"tool:{command}",
                "ok" if resolved else "error",
                resolved or f"{command} is not on PATH",
            )
        )

    repo_check, open_code = _check_repo_config(repo)
    checks.append(repo_check)
    checks.extend(_check_policy(repo))
    checks.append(_grant_check(repo))


    try:
        resolved_repo = _resolve_github_repo(repo, github_repo, runner=runner)
        checks.append(DoctorCheck("github-repository", "ok", resolved_repo))
        checks.append(_label_check(repo, resolved_repo, runner=runner))
    except Exception as exc:
        checks.append(DoctorCheck("github-repository", "error", str(exc)))

    checks.extend(_check_opencode(repo, enabled=open_code, runner=runner, which=which))
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
        marker = {"ok": "OK", "info": "INFO", "error": "ERROR"}.get(
            item.state,
            item.state.upper(),
        )
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

    install_parser = sub.add_parser("install")
    install_parser.add_argument("--repo", default=".")
    install_parser.add_argument("--github-repo", default="")
    install_parser.add_argument("--no-opencode", action="store_true")
    install_parser.add_argument("--json", action="store_true")

    labels_parser = sub.add_parser("ensure-labels")
    labels_parser.add_argument("--repo", default=".")
    labels_parser.add_argument("--github-repo", default="")
    labels_parser.add_argument("--json", action="store_true")

    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--repo", default=".")
    doctor_parser.add_argument("--github-repo", default="")
    doctor_parser.add_argument("--fix", action="store_true")
    doctor_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install_repo(
                Path(args.repo),
                github_repo=args.github_repo,
                enable_opencode=not args.no_opencode,
                runner=runner,
            )
            if args.json:
                print(json.dumps(result.to_json(), sort_keys=True))
            else:
                print(
                    f"Installed AutoDev repository assets in {result.repository}; "
                    f"GitHub labels ready for {result.github_repository}."
                )
            return 0
        if args.command == "ensure-labels":
            created = ensure_labels(
                Path(args.repo),
                github_repo=args.github_repo,
                runner=runner,
            )
            if args.json:
                print(json.dumps({"created": list(created), "state": "OK"}, sort_keys=True))
            else:
                print(f"Queue labels ensured; created={len(created)}.")
            return 0

        result = doctor(
            Path(args.repo),
            fix=bool(args.fix),
            github_repo=args.github_repo,
            runner=runner,
            which=which,
        )
        print(json.dumps(result.to_json(), sort_keys=True) if args.json else _render_doctor(result))
        return 0 if result.healthy else 2
    except (
        RepoSetupError,
        queue_contract.QueueError,
        privacy.PrivacyError,
        queue_selection.RoadmapError,
        opencode_adapter_contract.OpenCodeAdapterError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run_cli())

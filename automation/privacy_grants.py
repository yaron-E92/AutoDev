from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from automation import privacy


STORE_VERSION = 1
STORE_ENV = "AUTODEV_PRIVACY_GRANTS_PATH"
REPOSITORY_ID_ENV = "AUTODEV_PRIVACY_REPOSITORY_ID"
DEFAULT_STORE = Path(".autodev") / "privacy-grants.json"
DURATION_DELTAS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
DURATIONS = ("run", "24h", "7d", "30d", "until-revoked")
SCOPES = ("configured", "provider", "exact")
_BYPASS_DEPTH = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _store_path() -> Path:
    explicit = os.environ.get(STORE_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path.home() / DEFAULT_STORE).resolve()


def _load_store() -> dict[str, object]:
    path = _store_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": STORE_VERSION, "grants": []}
    if not isinstance(value, dict) or value.get("schema_version") != STORE_VERSION:
        return {"schema_version": STORE_VERSION, "grants": []}
    grants = value.get("grants", [])
    if not isinstance(grants, list):
        grants = []
    return {
        "schema_version": STORE_VERSION,
        "grants": [item for item in grants if isinstance(item, dict)],
    }


def _save_store(value: dict[str, object]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": STORE_VERSION,
        "updated_at": _iso(_now()),
        "grants": [
            item for item in value.get("grants", []) if isinstance(item, dict)
        ],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _normalize_github_remote(value: str) -> str:
    remote = value.strip()
    if remote.startswith("git@github.com:"):
        path = remote.split(":", 1)[1]
    else:
        parsed = urlparse(remote)
        if (parsed.hostname or "").casefold() != "github.com":
            return ""
        path = parsed.path.lstrip("/")
    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return ""
    return f"github:{parts[0].casefold()}/{parts[1].casefold()}"


def repository_identity(repo: Path, *, runner=subprocess.run) -> str:
    explicit = os.environ.get(REPOSITORY_ID_ENV, "").strip()
    if explicit:
        return explicit
    owner = os.environ.get("GITHUB_OWNER", "").strip()
    name = os.environ.get("GITHUB_REPO", "").strip()
    if owner and name:
        return f"github:{owner.casefold()}/{name.casefold()}"
    try:
        completed = runner(
            ["git", "remote", "get-url", "origin"],
            cwd=repo,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except OSError:
        completed = None
    if completed is not None and int(getattr(completed, "returncode", 1)) == 0:
        identity = _normalize_github_remote(
            str(getattr(completed, "stdout", "") or "")
        )
        if identity:
            return identity
    digest = hashlib.sha256(
        str(repo.expanduser().resolve()).encode("utf-8")
    ).hexdigest()
    return f"path:{digest}"


def _policy_fingerprint(
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
) -> str:
    source = {
        "profile": policy.profile,
        "consent_mode": policy.consent_mode,
        "policy_reviewed_at": privacy.POLICY_REVIEWED_AT,
        "policy_source": decision.policy_source,
        "training": decision.training,
        "retention": decision.retention,
        "retention_duration": decision.retention_duration,
        "enforcement_state": decision.enforcement_state,
        "controls": sorted(decision.controls),
        "attestations": sorted(decision.attestations),
        "provider_attestations": policy.provider_attestations,
    }
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _route_identity(
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
) -> str:
    source = {
        "role": decision.role,
        "provider": decision.provider,
        "route": decision.route,
        "model": decision.model,
        "route_scope": decision.route_scope,
        "policy_fingerprint": _policy_fingerprint(policy, decision),
        "reason": decision.reason,
    }
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _provider_identity(
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
) -> str:
    source = {
        "provider": decision.provider,
        "route_scope": decision.route_scope,
        "policy_fingerprint": _policy_fingerprint(policy, decision),
        "reason": decision.reason,
    }
    return hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _grant_id(record: dict[str, object]) -> str:
    source = {
        key: record.get(key)
        for key in (
            "repository_id",
            "scope",
            "route_identities",
            "provider_identities",
            "granted_at",
            "expires_at",
        )
    }
    digest = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"pg-{digest[:16]}"


def _status(
    record: dict[str, object],
    *,
    now: datetime | None = None,
) -> str:
    if str(record.get("revoked_at", "")).strip():
        return "revoked"
    expires = _parse_time(record.get("expires_at"))
    if expires is not None and (now or _now()) >= expires:
        return "expired"
    return "active"


def _grant_matches(
    record: dict[str, object],
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
    *,
    now: datetime | None = None,
) -> bool:
    if _status(record, now=now) != "active":
        return False
    if str(record.get("repository_id", "")) != repository_identity(repo):
        return False
    scope = str(record.get("scope", ""))
    if scope in {"exact-route", "configured-routes"}:
        identities = record.get("route_identities", [])
        return (
            isinstance(identities, list)
            and _route_identity(policy, decision) in identities
        )
    if scope == "provider-policy":
        identities = record.get("provider_identities", [])
        return (
            isinstance(identities, list)
            and _provider_identity(policy, decision) in identities
        )
    return False


def matching_grant(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
    *,
    now: datetime | None = None,
) -> dict[str, object] | None:
    if (
        _BYPASS_DEPTH
        or not policy.enabled
        or policy.local_only
        or policy.consent_mode != "explicit"
    ):
        return None
    for record in reversed(_load_store().get("grants", [])):
        if (
            isinstance(record, dict)
            and _grant_matches(record, repo, policy, decision, now=now)
        ):
            return record
    return None


@contextmanager
def bypass_grants():
    global _BYPASS_DEPTH
    _BYPASS_DEPTH += 1
    try:
        yield
    finally:
        _BYPASS_DEPTH -= 1


def create_grant(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decisions: list[privacy.PrivacyDecision],
    *,
    duration: str,
    scope: str = "configured",
    now: datetime | None = None,
) -> dict[str, object]:
    if duration not in DURATION_DELTAS and duration != "until-revoked":
        raise privacy.PrivacyError(
            f"unsupported persistent consent duration: {duration}"
        )
    if scope not in SCOPES:
        raise privacy.PrivacyError(f"unsupported persistent consent scope: {scope}")
    if not decisions:
        raise privacy.PrivacyError(
            "cannot create a privacy grant without consent-required routes"
        )
    if (
        not policy.enabled
        or policy.local_only
        or policy.consent_mode != "explicit"
    ):
        raise privacy.PrivacyError(
            "repository privacy policy does not permit consent exceptions"
        )
    if scope == "exact" and len(decisions) != 1:
        raise privacy.PrivacyError(
            "exact-route consent requires exactly one selected route"
        )

    granted = (now or _now()).astimezone(timezone.utc)
    expires = (
        granted + DURATION_DELTAS[duration]
        if duration in DURATION_DELTAS
        else None
    )
    record: dict[str, object] = {
        "repository_id": repository_identity(repo),
        "scope": {
            "configured": "configured-routes",
            "provider": "provider-policy",
            "exact": "exact-route",
        }[scope],
        "privacy_profile": policy.profile,
        "duration": duration,
        "granted_at": _iso(granted),
        "expires_at": _iso(expires) if expires is not None else "",
        "until_revoked": expires is None,
        "route_identities": (
            sorted({_route_identity(policy, item) for item in decisions})
            if scope in {"configured", "exact"}
            else []
        ),
        "provider_identities": (
            sorted({_provider_identity(policy, item) for item in decisions})
            if scope == "provider"
            else []
        ),
        "roles": sorted({item.role for item in decisions}),
        "routes": sorted({item.route for item in decisions}),
        "providers": sorted({item.provider for item in decisions}),
        "policy_fingerprints": sorted(
            {_policy_fingerprint(policy, item) for item in decisions}
        ),
        "unmet_requirements": sorted({item.reason for item in decisions}),
    }
    record["id"] = _grant_id(record)
    store = _load_store()
    grants = [
        item for item in store.get("grants", []) if isinstance(item, dict)
    ]
    grants.append(record)
    store["grants"] = grants
    _save_store(store)
    return record


def revoke_grants(
    repo: Path,
    *,
    grant_id: str = "",
    revoke_all: bool = False,
) -> int:
    repository_id = repository_identity(repo)
    store = _load_store()
    grants = [
        item for item in store.get("grants", []) if isinstance(item, dict)
    ]
    candidates = [
        item
        for item in grants
        if str(item.get("repository_id", "")) == repository_id
        and _status(item) == "active"
    ]
    if not revoke_all:
        matches = [
            item for item in candidates if str(item.get("id", "")) == grant_id
        ]
        if not matches and grant_id:
            prefix_matches = [
                item
                for item in candidates
                if str(item.get("id", "")).startswith(grant_id)
            ]
            if len(prefix_matches) == 1:
                matches = prefix_matches
        candidates = matches
    revoked_at = _iso(_now())
    for item in candidates:
        item["revoked_at"] = revoked_at
    if candidates:
        store["grants"] = grants
        _save_store(store)
    return len(candidates)


def current_grants(repo: Path) -> list[dict[str, object]]:
    repository_id = repository_identity(repo)
    result: list[dict[str, object]] = []
    for item in _load_store().get("grants", []):
        if (
            not isinstance(item, dict)
            or str(item.get("repository_id", "")) != repository_id
        ):
            continue
        record = dict(item)
        record["status"] = _status(item)
        result.append(record)
    return result


def _audit_grant_use(
    repo: Path,
    decision: privacy.PrivacyDecision,
    record: dict[str, object],
) -> None:
    current = repo / ".autodev-run" / "current"
    path = (
        current if current.exists() else repo / ".autodev-run"
    ) / privacy.PRIVACY_AUDIT
    payload = decision.safe_metadata()
    payload.update(
        {
            "event": "persistent-consent-use",
            "consent_reference": str(record.get("id", "")),
            "consent_grant_scope": str(record.get("scope", "")),
            "consent_duration": str(record.get("duration", "")),
            "consent_expires_at": str(record.get("expires_at", ""))
            or "until-revoked",
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        return


def _install_privacy_gate() -> None:
    current = privacy._consent_or_block
    if getattr(current, "_autodev_persistent_grants", False):
        return
    original = current

    def consent_or_block(
        repo: Path,
        policy: privacy.PrivacyPolicy,
        decision: privacy.PrivacyDecision,
        consent_reader,
    ) -> privacy.PrivacyDecision:
        record = matching_grant(repo, policy, decision)
        if record is not None:
            decision.outcome = "ALLOW"
            decision.enforcement_state = "user-consented"
            expiry = str(record.get("expires_at", "")) or "until revoked"
            decision.consent_scope = (
                f"pre-authorized {record.get('scope', 'persistent')} grant "
                f"{record.get('id', '')} until {expiry}"
            )
            privacy._audit(repo, decision)
            _audit_grant_use(repo, decision, record)
            return decision
        return original(repo, policy, decision, consent_reader)

    consent_or_block._autodev_persistent_grants = True  # type: ignore[attr-defined]
    privacy._consent_or_block = consent_or_block


def _persistent_duration_from_choice(choice: str) -> str:
    normalized = choice.strip().casefold()
    if normalized in {"1", "24", "24h", "day"}:
        return "24h"
    if normalized in {"7", "7d", "week"}:
        return "7d"
    if normalized in {"3", "30", "30d", "month"}:
        return "30d"
    if normalized in {"u", "until", "until-revoked", "until revoked"}:
        return "until-revoked"
    return ""


def _read_run_choice(required, privacy_consent) -> str | None:
    prompt = (
        "\nChoose [A] this run, [R] review each call this run, [1] 24 hours, "
        "[7] 7 days, [3] 30 days, [U] until revoked, or [N] deny: "
    )
    if sys.stdin is not None and sys.stdin.isatty():
        privacy_consent._write_run_consent_table(sys.stdout, required)
        return str(input(prompt) or "").strip().casefold()
    with privacy_consent._controlling_terminal() as console:
        if console is None:
            return None
        reader, writer = console
        privacy_consent._write_run_consent_table(writer, required)
        writer.write(prompt)
        writer.flush()
        answer = reader.readline()
        return (
            str(answer or "").strip().casefold()
            if answer != ""
            else None
        )


def _install_run_consent_hook() -> None:
    from automation import privacy_consent

    current = privacy_consent.ensure_run_consent
    if getattr(current, "_autodev_persistent_grants", False):
        return
    original = current

    def ensure_run_consent(
        repo: Path,
        mappings: dict[str, dict[str, str]],
        *,
        executable: str,
        runner=subprocess.run,
    ) -> None:
        repo = repo.expanduser().resolve()
        policy = privacy.load_policy(repo)
        if (
            not policy.enabled
            or policy.local_only
            or policy.consent_mode != "explicit"
        ):
            return original(
                repo, mappings, executable=executable, runner=runner
            )

        existing = privacy_consent._load_ledger(repo)
        interaction_mode = (
            str(existing.get("interaction_mode", "")) if existing else ""
        )
        if interaction_mode in {
            "batch",
            "per-call",
            "verified-only",
            "noninteractive-exact",
            "denied",
        }:
            return original(
                repo, mappings, executable=executable, runner=runner
            )

        with bypass_grants():
            raw_required = privacy_consent._known_consent_requirements(
                repo,
                mappings,
                executable=executable,
                runner=runner,
            )
        if not raw_required:
            return original(
                repo, mappings, executable=executable, runner=runner
            )

        uncovered = [
            item
            for item in raw_required
            if matching_grant(repo, policy, item) is None
        ]
        if not uncovered:
            # Do not materialize a run-scoped approval. Re-evaluate persistent
            # grants before every role so expiry/revocation is immediate.
            return

        if privacy_consent._all_covered_by_environment(uncovered):
            return original(
                repo, mappings, executable=executable, runner=runner
            )

        choice = _read_run_choice(uncovered, privacy_consent)
        if choice is None:
            return original(
                repo, mappings, executable=executable, runner=runner
            )

        duration = _persistent_duration_from_choice(choice)
        if duration:
            create_grant(
                repo,
                policy,
                uncovered,
                duration=duration,
                scope="configured",
            )
            return
        if choice in {"a", "approve", "all", "run"}:
            privacy_consent._save_ledger(
                repo,
                {
                    "interaction_mode": "batch",
                    "created_at": privacy_consent._now(),
                    "approvals": [
                        privacy_consent._approval_record(
                            repo, policy, item, mode="batch"
                        )
                        for item in uncovered
                    ],
                },
            )
            return
        if choice in {"r", "review", "one-by-one", "one by one"}:
            privacy_consent._save_ledger(
                repo,
                {
                    "interaction_mode": "per-call",
                    "created_at": privacy_consent._now(),
                    "approvals": [],
                },
            )
            return

        privacy_consent._save_ledger(
            repo,
            {
                "interaction_mode": "denied",
                "created_at": privacy_consent._now(),
                "approvals": [],
            },
        )
        raise privacy.PrivacyError(
            "privacy consent denied; AutoDev stopped before sending repository/run content"
        )

    ensure_run_consent._autodev_persistent_grants = True  # type: ignore[attr-defined]
    privacy_consent.ensure_run_consent = ensure_run_consent


def install(*, run_consent: bool = False) -> None:
    _install_privacy_gate()
    if run_consent:
        _install_run_consent_hook()


def _resolve_requirements(
    repo: Path,
    *,
    runner=subprocess.run,
    which=None,
):
    from automation import opencode_adapter, opencode_coordinator, privacy_consent

    executable = opencode_coordinator.opencode_cli.resolve_opencode_cli(which=which)
    mappings = opencode_adapter.resolve_opencode_model_mappings(
        repo, runner=runner, which=which
    )
    with bypass_grants():
        required = privacy_consent._known_consent_requirements(
            repo,
            mappings,
            executable=executable,
            runner=runner,
        )
    return required


def _select_scope_decisions(
    required: list[privacy.PrivacyDecision],
    *,
    scope: str,
    role: str,
) -> list[privacy.PrivacyDecision]:
    if role:
        selected = [item for item in required if item.role == role]
        if not selected:
            raise privacy.PrivacyError(
                f"configured role does not require consent: {role}"
            )
    else:
        selected = list(required)
    if scope == "exact" and len(selected) != 1:
        roles = ", ".join(item.role for item in selected)
        raise privacy.PrivacyError(
            "exact scope requires exactly one route; specify --role from: " + roles
        )
    return selected


def _prompt_duration() -> str:
    answer = str(
        input(
            "Duration: [R] this run, [1] 24 hours, [7] 7 days, [3] 30 days, "
            "[U] until revoked, [N] reject: "
        )
        or ""
    ).strip().casefold()
    if answer in {"r", "run"}:
        return "run"
    if answer in {"n", "no", "reject", "deny", ""}:
        return ""
    return _persistent_duration_from_choice(answer)


def _run_consent_cli(
    repo: Path,
    args: argparse.Namespace,
    *,
    runner=subprocess.run,
    which=None,
) -> int:
    from automation import privacy_consent

    if sys.stdin is None or not sys.stdin.isatty():
        print(
            "privacy consent creation requires an interactive terminal; "
            "headless/scheduled runs may only consume existing grants",
            file=sys.stderr,
        )
        return 2
    policy = privacy.load_policy(repo)
    if not policy.enabled:
        print(
            "Privacy policy is disabled for this repository; "
            "no consent grant is needed."
        )
        return 0
    if policy.local_only or policy.consent_mode != "explicit":
        print(
            "Repository privacy policy forbids consent exceptions.",
            file=sys.stderr,
        )
        return 2
    try:
        required = _resolve_requirements(
            repo, runner=runner, which=which
        )
    except Exception as exc:
        print(
            f"Cannot resolve configured privacy routes: {exc}",
            file=sys.stderr,
        )
        return 2
    if not required:
        print(
            "All currently configured routes satisfy the repository privacy policy; "
            "no consent grant is needed."
        )
        return 0
    try:
        selected = _select_scope_decisions(
            required, scope=args.scope, role=args.role
        )
    except privacy.PrivacyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    privacy_consent._write_run_consent_table(sys.stdout, selected)
    duration = args.duration or _prompt_duration()
    if not duration:
        print("Privacy consent was not granted.", file=sys.stderr)
        return 1
    if duration == "run":
        if not privacy_consent._run_id(repo):
            print(
                "Run-scoped consent requires an active AutoDev run.",
                file=sys.stderr,
            )
            return 2
        privacy_consent._save_ledger(
            repo,
            {
                "interaction_mode": "batch",
                "created_at": privacy_consent._now(),
                "approvals": [
                    privacy_consent._approval_record(
                        repo, policy, item, mode="batch"
                    )
                    for item in selected
                ],
            },
        )
        print("Granted exact consent for the current AutoDev run only.")
        return 0

    try:
        record = create_grant(
            repo,
            policy,
            selected,
            duration=duration,
            scope=args.scope,
        )
    except privacy.PrivacyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    expiry = str(record.get("expires_at", "")) or "until revoked"
    print(
        f"Granted {record['id']} ({record['scope']}) for {duration}; "
        f"valid {expiry}."
    )
    return 0


def _run_status_cli(repo: Path, args: argparse.Namespace) -> int:
    grants = current_grants(repo)
    if args.json:
        print(
            json.dumps(
                {
                    "repository_id": repository_identity(repo),
                    "grants": grants,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    print(f"Privacy grant store: {_store_path()}")
    print(f"Repository: {repository_identity(repo)}")
    if not grants:
        print("No privacy grants recorded for this repository.")
        return 0
    for item in grants:
        expiry = str(item.get("expires_at", "")) or "until revoked"
        print(
            f"{item.get('id', '')}  {item.get('status', ''):<7}  "
            f"{item.get('scope', ''):<18} duration={item.get('duration', '')} "
            f"expires={expiry}"
        )
        routes = item.get("routes", [])
        providers = item.get("providers", [])
        if isinstance(routes, list) and routes:
            print("  routes=" + ", ".join(str(value) for value in routes))
        elif isinstance(providers, list) and providers:
            print(
                "  providers=" + ", ".join(str(value) for value in providers)
            )
    return 0


def _run_revoke_cli(repo: Path, args: argparse.Namespace) -> int:
    if not args.all and not args.grant_id:
        print("Specify a grant id or --all.", file=sys.stderr)
        return 2
    count = revoke_grants(
        repo,
        grant_id=args.grant_id,
        revoke_all=args.all,
    )
    if count == 0:
        print(
            "No matching active privacy grant was found.",
            file=sys.stderr,
        )
        return 1
    print(f"Revoked {count} privacy grant{'s' if count != 1 else ''}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodev privacy")
    subparsers = parser.add_subparsers(dest="action", required=True)

    status = subparsers.add_parser(
        "status",
        help="Show persistent privacy grants for this repository.",
    )
    status.add_argument("--json", action="store_true")

    consent = subparsers.add_parser(
        "consent",
        help="Create an explicit run or time-bounded privacy consent grant.",
    )
    consent.add_argument("--duration", choices=DURATIONS, default="")
    consent.add_argument("--scope", choices=SCOPES, default="configured")
    consent.add_argument(
        "--role",
        choices=(
            "reader",
            "synthesizer",
            "planner",
            "implementer",
            "fixer",
            "verifier",
        ),
        default="",
    )

    revoke = subparsers.add_parser(
        "revoke",
        help="Immediately revoke persistent privacy grants.",
    )
    revoke.add_argument("grant_id", nargs="?", default="")
    revoke.add_argument("--all", action="store_true")
    return parser


def run_cli(
    argv: list[str],
    *,
    repo: Path | None = None,
    runner=subprocess.run,
    which=None,
) -> int:
    args = _parser().parse_args(argv)
    target = (repo or privacy.privacy_repo()).expanduser().resolve()
    if args.action == "status":
        return _run_status_cli(target, args)
    if args.action == "consent":
        return _run_consent_cli(
            target, args, runner=runner, which=which
        )
    if args.action == "revoke":
        return _run_revoke_cli(target, args)
    return 2

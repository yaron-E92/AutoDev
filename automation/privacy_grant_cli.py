from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from automation import privacy, role_runtime

from automation.privacy_grant_commands import (
    create_grant,
    current_grants,
    revoke_grants,
)
from automation.privacy_grant_contract import (
    DURATIONS,
    SCOPES,
)
from automation.privacy_grant_hooks import (
    _persistent_duration_from_choice,
)
from automation.privacy_grant_store import (
    _store_path,
    repository_identity,
)

def _resolve_requirements(
    repo: Path,
    *,
    runner=subprocess.run,
    which=None,
):
    runtime, _ = role_runtime.select_runtime(repo)
    evidence = runtime.privacy_evidence(
        repo,
        runner=runner,
        which=which,
    )
    blocked = [
        item
        for item in evidence.values()
        if isinstance(item, privacy.PrivacyDecision) and item.outcome == "BLOCK"
    ]
    if blocked:
        first = blocked[0]
        raise privacy.PrivacyError(
            f"repository privacy policy forbids {first.role} route {first.route}: "
            f"{first.reason}"
        )
    return [
        item
        for item in evidence.values()
        if isinstance(item, privacy.PrivacyDecision)
        and item.outcome == "CONSENT_REQUIRED"
    ]

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

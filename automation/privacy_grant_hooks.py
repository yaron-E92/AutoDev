from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from automation import privacy

from automation.privacy_grant_commands import (
    create_grant,
)
from automation.privacy_grant_matching import (
    bypass_grants,
    matching_grant,
)

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
    # Persistent grants are enforced by privacy_authorization for every runtime.
    # This module now only extends the interactive run-consent UX.
    if run_consent:
        _install_run_consent_hook()

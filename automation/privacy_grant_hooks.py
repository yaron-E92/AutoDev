from __future__ import annotations

import json
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

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from automation import privacy
from automation.privacy_grant_matching import matching_grant


class PrivacyConsentRequired(privacy.PrivacyError):
    def __init__(self, decisions: Iterable[privacy.PrivacyDecision]) -> None:
        self.decisions = tuple(decisions)
        routes = ", ".join(
            f"{item.role}={item.route}" for item in self.decisions
        )
        super().__init__(
            f"privacy consent is required for: {routes}",
            classification="privacy_blocked",
        )


def _audit_grant_use(
    repo: Path,
    decision: privacy.PrivacyDecision,
    record: dict[str, object],
) -> None:
    current = repo / ".autodev-run" / "current"
    path = (current if current.exists() else repo / ".autodev-run") / privacy.PRIVACY_AUDIT
    payload = decision.safe_metadata()
    payload.update(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "persistent-consent-use",
            "consent_reference": str(record.get("id", "")),
            "consent_grant_scope": str(record.get("scope", "")),
            "consent_duration": str(record.get("duration", "")),
            "consent_expires_at": str(record.get("expires_at", "")) or "until-revoked",
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        pass


def _apply_persistent_grant(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
) -> privacy.PrivacyDecision | None:
    record = matching_grant(repo, policy, decision)
    if record is None:
        return None
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


def authorize_evaluated(
    repo: Path,
    decision: privacy.PrivacyDecision,
    *,
    consent_reader=None,
    headless: bool = False,
) -> privacy.PrivacyDecision:
    """Authorize runtime/provider-neutral privacy evidence."""
    repo = repo.expanduser().resolve()
    policy = privacy.load_policy(repo)

    if decision.outcome == "ALLOW":
        privacy._audit(repo, decision)
        return decision
    if decision.outcome == "BLOCK":
        return privacy._block(repo, decision)
    if decision.outcome != "CONSENT_REQUIRED":
        raise privacy.PrivacyError(
            f"invalid privacy decision outcome {decision.outcome!r} for "
            f"{decision.role} route {decision.route}"
        )

    granted = _apply_persistent_grant(repo, policy, decision)
    if granted is not None:
        return granted
    if headless:
        raise PrivacyConsentRequired([decision])
    return privacy._consent_or_block(repo, policy, decision, consent_reader)


def authorize_headless(
    repo: Path,
    decisions: Iterable[privacy.PrivacyDecision],
) -> tuple[privacy.PrivacyDecision, ...]:
    """Authorize evaluated routes without creating or widening consent."""
    repo = repo.expanduser().resolve()
    policy = privacy.load_policy(repo)
    allowed: list[privacy.PrivacyDecision] = []
    uncovered: list[privacy.PrivacyDecision] = []

    for decision in decisions:
        if decision.outcome == "ALLOW":
            privacy._audit(repo, decision)
            allowed.append(decision)
            continue
        if decision.outcome == "BLOCK":
            privacy._block(repo, decision)
        if decision.outcome != "CONSENT_REQUIRED":
            raise privacy.PrivacyError(
                f"invalid privacy decision outcome {decision.outcome!r} for "
                f"{decision.role} route {decision.route}"
            )
        granted = _apply_persistent_grant(repo, policy, decision)
        if granted is not None:
            allowed.append(granted)
        else:
            uncovered.append(decision)

    if uncovered:
        raise PrivacyConsentRequired(uncovered)
    return tuple(allowed)

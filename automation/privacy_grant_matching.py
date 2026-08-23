from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from automation import privacy

from automation.privacy_grant_contract import (
    _BYPASS_DEPTH,
)
from automation.privacy_grant_store import (
    _load_store,
    _now,
    _parse_time,
    repository_identity,
)

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

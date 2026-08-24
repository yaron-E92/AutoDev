from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from automation import privacy

from automation.privacy_grant_contract import (
    DURATION_DELTAS,
    SCOPES,
)
from automation.privacy_grant_matching import (
    _grant_id,
    _policy_fingerprint,
    _provider_identity,
    _route_identity,
    _status,
)
from automation.privacy_grant_store import (
    _iso,
    _load_store,
    _now,
    _save_store,
    repository_identity,
)

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

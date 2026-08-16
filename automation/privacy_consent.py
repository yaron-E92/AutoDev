from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from automation import opencode_adapter, opencode_coordinator, opencode_resume, privacy, run_manifest, workflow_stages


LEDGER_NAME = "privacy-consent.json"
LEDGER_VERSION = 1
ROLE_NAMES = ("reader", "synthesizer", "planner", "implementer", "fixer", "verifier")
_PREVIEW_DEPTH = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ledger_path(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_stages.CURRENT_DIR / LEDGER_NAME


def _manifest(repo: Path) -> dict[str, object]:
    path = opencode_resume.manifest_path(repo)
    if not path.is_file():
        return {}
    try:
        return run_manifest.load_manifest(path)
    except (OSError, ValueError, run_manifest.ManifestError):
        return {}


def _run_id(repo: Path) -> str:
    return str(_manifest(repo).get("run_id", ""))


def _load_ledger(repo: Path) -> dict[str, object]:
    path = _ledger_path(repo)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get("schema_version") != LEDGER_VERSION:
        return {}
    if str(value.get("run_id", "")) != _run_id(repo):
        return {}
    return value


def _save_ledger(repo: Path, value: dict[str, object]) -> None:
    path = _ledger_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(value)
    payload["schema_version"] = LEDGER_VERSION
    payload["run_id"] = _run_id(repo)
    payload["updated_at"] = _now()
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _policy_fingerprint(policy: privacy.PrivacyPolicy, decision: privacy.PrivacyDecision) -> str:
    # This is evaluated before consent mutates the decision to user-consented. In particular,
    # request-verified vs unverified is execution-affecting privacy evidence and must invalidate consent.
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


def consent_identity(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
) -> tuple[str, str]:
    policy_fingerprint = _policy_fingerprint(policy, decision)
    source = {
        "run_id": _run_id(repo),
        "role": decision.role,
        "provider": decision.provider,
        "route": decision.route,
        "model": decision.model,
        "route_scope": decision.route_scope,
        "policy_fingerprint": policy_fingerprint,
        "reason": decision.reason,
    }
    identity = hashlib.sha256(
        json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return identity, policy_fingerprint


def _approval_record(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
    *,
    mode: str,
) -> dict[str, object]:
    identity, policy_fingerprint = consent_identity(repo, policy, decision)
    return {
        "identity": identity,
        "policy_fingerprint": policy_fingerprint,
        "role": decision.role,
        "provider": decision.provider,
        "route": decision.route,
        "model": decision.model,
        "route_scope": decision.route_scope,
        "training": decision.training,
        "retention": decision.retention,
        "retention_duration": decision.retention_duration,
        "policy_source": decision.policy_source,
        "policy_checked_at": privacy.POLICY_REVIEWED_AT,
        "enforcement_state": decision.enforcement_state,
        "reason": decision.reason,
        "mode": mode,
        "scope": "this-run",
        "approved_at": _now(),
    }


def _approvals(ledger: dict[str, object]) -> list[dict[str, object]]:
    raw = ledger.get("approvals", [])
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _approved_record(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decision: privacy.PrivacyDecision,
) -> dict[str, object] | None:
    identity, _ = consent_identity(repo, policy, decision)
    for item in _approvals(_load_ledger(repo)):
        if str(item.get("identity", "")) == identity:
            return item
    return None


def _persist_approval(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    preconsent_decision: privacy.PrivacyDecision,
    *,
    mode: str,
) -> None:
    ledger = _load_ledger(repo)
    if not ledger:
        ledger = {
            "schema_version": LEDGER_VERSION,
            "run_id": _run_id(repo),
            "interaction_mode": "per-call" if mode == "per-call" else mode,
            "created_at": _now(),
            "approvals": [],
        }
    record = _approval_record(repo, policy, preconsent_decision, mode=mode)
    approvals = [
        item
        for item in _approvals(ledger)
        if str(item.get("identity", "")) != str(record["identity"])
    ]
    approvals.append(record)
    ledger["approvals"] = approvals
    _save_ledger(repo, ledger)


def _display_row(decision: privacy.PrivacyDecision) -> str:
    retention = decision.retention
    if decision.retention_duration:
        retention += f" ({decision.retention_duration})"
    return (
        f"{decision.role:<13} {decision.provider:<18} {decision.route:<34} "
        f"{decision.training:<10} {retention}"
    )


def _preview_decision(
    repo: Path,
    *,
    role: str,
    model: str,
    executable: str,
    runner,
) -> privacy.PrivacyDecision:
    global _PREVIEW_DEPTH
    _PREVIEW_DEPTH += 1
    try:
        decision, _ = privacy.authorize_opencode_role(
            repo,
            role=role,
            model=model,
            opencode_cli=executable,
            runner=runner,
            base_env=dict(os.environ),
            consent_reader=lambda _: "no",
        )
        return decision
    finally:
        _PREVIEW_DEPTH -= 1


def _known_consent_requirements(
    repo: Path,
    mappings: dict[str, dict[str, str]],
    *,
    executable: str,
    runner,
) -> list[privacy.PrivacyDecision]:
    required: list[privacy.PrivacyDecision] = []
    for role in ROLE_NAMES:
        model = str(mappings.get(role, {}).get("model", "")).strip()
        if not model:
            continue
        decision = _preview_decision(
            repo,
            role=role,
            model=model,
            executable=executable,
            runner=runner,
        )
        if decision.outcome == "CONSENT_REQUIRED":
            required.append(decision)
    return required


def _all_covered_by_environment(decisions: list[privacy.PrivacyDecision]) -> bool:
    return bool(decisions) and all(
        privacy._consent_env(item.role, item.route) for item in decisions
    )


def _persist_environment_approvals(
    repo: Path,
    policy: privacy.PrivacyPolicy,
    decisions: list[privacy.PrivacyDecision],
) -> None:
    _save_ledger(
        repo,
        {
            "schema_version": LEDGER_VERSION,
            "run_id": _run_id(repo),
            "interaction_mode": "noninteractive-exact",
            "created_at": _now(),
            "approvals": [
                _approval_record(repo, policy, item, mode="environment")
                for item in decisions
            ],
        },
    )


def ensure_run_consent(
    repo: Path,
    mappings: dict[str, dict[str, str]],
    *,
    executable: str,
    runner=subprocess.run,
) -> None:
    repo = repo.expanduser().resolve()
    run_id = _run_id(repo)
    if not run_id:
        return
    policy = privacy.load_policy(repo)
    if not policy.enabled:
        return

    existing = _load_ledger(repo)
    interaction_mode = str(existing.get("interaction_mode", "")) if existing else ""
    if interaction_mode in {"batch", "per-call", "verified-only", "noninteractive-exact"}:
        return
    if interaction_mode == "denied":
        raise privacy.PrivacyError("privacy consent was denied for this AutoDev run")

    required = _known_consent_requirements(
        repo,
        mappings,
        executable=executable,
        runner=runner,
    )
    if not required:
        _save_ledger(
            repo,
            {
                "schema_version": LEDGER_VERSION,
                "run_id": run_id,
                "interaction_mode": "verified-only",
                "created_at": _now(),
                "approvals": [],
            },
        )
        return

    if policy.local_only:
        raise privacy.PrivacyError(
            "repository privacy profile is local-only; cloud exceptions are forbidden"
        )

    if _all_covered_by_environment(required):
        _persist_environment_approvals(repo, policy, required)
        return

    if sys.stdin is None or not sys.stdin.isatty():
        raise privacy.PrivacyError(
            "privacy consent is required for one or more AutoDev role routes, but the run is non-interactive; "
            "provide exact role=route entries through AUTODEV_PRIVACY_CONSENT or run interactively"
        )

    print(
        f"{len(required)} role route{'s' if len(required) != 1 else ''} require explicit privacy consent for this run:\n",
        flush=True,
    )
    print(
        f"{'Role':<13} {'Provider':<18} {'Route/model':<34} {'Training':<10} Retention",
        flush=True,
    )
    for decision in required:
        print(_display_row(decision), flush=True)
        print(
            "  "
            + f"scope={decision.route_scope}; policy={decision.policy_source or 'unknown'}; "
            + f"checked={privacy.POLICY_REVIEWED_AT}; reason={decision.reason}",
            flush=True,
        )

    answer = str(
        input(
            "\nChoose [A] approve every exact combination above for this run, "
            "[R] review each call individually, or [N] deny and abort: "
        )
        or ""
    ).strip().casefold()

    if answer in {"a", "approve", "all"}:
        _save_ledger(
            repo,
            {
                "schema_version": LEDGER_VERSION,
                "run_id": run_id,
                "interaction_mode": "batch",
                "created_at": _now(),
                "approvals": [
                    _approval_record(repo, policy, item, mode="batch")
                    for item in required
                ],
            },
        )
        return
    if answer in {"r", "review", "one-by-one", "one by one"}:
        _save_ledger(
            repo,
            {
                "schema_version": LEDGER_VERSION,
                "run_id": run_id,
                "interaction_mode": "per-call",
                "created_at": _now(),
                "approvals": [],
            },
        )
        return

    _save_ledger(
        repo,
        {
            "schema_version": LEDGER_VERSION,
            "run_id": run_id,
            "interaction_mode": "denied",
            "created_at": _now(),
            "approvals": [],
        },
    )
    raise privacy.PrivacyError(
        "privacy consent denied; AutoDev stopped before sending repository/run content"
    )


def _install_consent_gate() -> None:
    current = privacy._consent_or_block
    if getattr(current, "_autodev_run_consent", False):
        return
    original = current

    def consent_or_block(
        repo: Path,
        policy: privacy.PrivacyPolicy,
        decision: privacy.PrivacyDecision,
        consent_reader,
    ) -> privacy.PrivacyDecision:
        if _PREVIEW_DEPTH:
            return decision

        approved = _approved_record(repo, policy, decision)
        if approved is not None:
            mode = str(approved.get("mode", "batch"))
            decision.outcome = "ALLOW"
            decision.enforcement_state = "user-consented"
            decision.consent_scope = f"this run ({mode} consent)"
            privacy._audit(repo, decision)
            return decision

        preconsent_decision = copy.deepcopy(decision)
        environment_approved = privacy._consent_env(decision.role, decision.route)
        result = original(repo, policy, decision, consent_reader)
        if (
            result.outcome == "ALLOW"
            and result.enforcement_state == "user-consented"
            and _run_id(repo)
        ):
            mode = "environment" if environment_approved else "per-call"
            _persist_approval(repo, policy, preconsent_decision, mode=mode)
        return result

    consent_or_block._autodev_run_consent = True  # type: ignore[attr-defined]
    privacy._consent_or_block = consent_or_block


def _install_audit_preview_guard() -> None:
    current = privacy._audit
    if getattr(current, "_autodev_preview_guard", False):
        return
    original = current

    def audit(repo: Path, decision: privacy.PrivacyDecision) -> None:
        if _PREVIEW_DEPTH:
            return
        original(repo, decision)

    audit._autodev_preview_guard = True  # type: ignore[attr-defined]
    privacy._audit = audit


def _install_preflight_hook() -> None:
    current = opencode_coordinator._run_agent_process
    if getattr(current, "_autodev_run_consent", False):
        return
    original = current

    def run_agent_process(
        repo: Path,
        role: str,
        prompt: str,
        *,
        runner,
        which=None,
        repair_kind: str = "",
        phase: str = "work",
    ) -> None:
        try:
            executable = opencode_coordinator.opencode_cli.resolve_opencode_cli(which=which)
            mappings = opencode_adapter.resolve_opencode_model_mappings(
                repo, runner=runner, which=which
            )
            ensure_run_consent(
                repo,
                mappings,
                executable=executable,
                runner=runner,
            )
        except privacy.PrivacyError as exc:
            raise opencode_coordinator.OpenCodeCoordinatorError(
                str(exc), classification=exc.classification
            ) from exc
        return original(
            repo,
            role,
            prompt,
            runner=runner,
            which=which,
            repair_kind=repair_kind,
            phase=phase,
        )

    run_agent_process._autodev_run_consent = True  # type: ignore[attr-defined]
    opencode_coordinator._run_agent_process = run_agent_process


def install() -> None:
    _install_audit_preview_guard()
    _install_consent_gate()
    _install_preflight_hook()

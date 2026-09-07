from __future__ import annotations

import argparse
import json
from pathlib import Path


CURRENT_DIR = Path(".autodev-run") / "current"
ROLE_ATTEMPT_DIR = "role-attempts"
_NATIVE_MODES = {"native-strict", "native-validated"}


def evaluate(repo: Path) -> dict[str, object]:
    """Return a content-free aggregate for Structured Output rollout evaluation."""

    current = repo.expanduser().resolve() / CURRENT_DIR
    roles: dict[str, dict[str, object]] = {}
    totals = _empty_stats()

    attempt_dir = current / ROLE_ATTEMPT_DIR
    if attempt_dir.is_dir():
        for path in sorted(attempt_dir.glob("*.json")):
            record = _read_json(path)
            role = str(record.get("role", "") or "").strip()
            if not role:
                continue
            stats = roles.setdefault(role, _empty_stats())
            _accumulate(stats, record)
            _accumulate(totals, record)

    active_ux_roles: list[str] = []
    ux_evidence_roles: list[str] = []
    if current.is_dir():
        for context_path in sorted(current.glob("ux-context-*.json")):
            role = context_path.stem.removeprefix("ux-context-")
            context = _read_json(context_path)
            if not role or not _ux_context_active(context):
                continue
            active_ux_roles.append(role)
            if _has_mechanical_ux_evidence(current, role):
                ux_evidence_roles.append(role)

    return {
        "version": 1,
        "totals": totals,
        "roles": {role: roles[role] for role in sorted(roles)},
        "ux": {
            "active_roles": sorted(set(active_ux_roles)),
            "evidence_roles": sorted(set(ux_evidence_roles)),
            "missing_evidence_roles": sorted(
                set(active_ux_roles) - set(ux_evidence_roles)
            ),
        },
    }


def _empty_mode_stats() -> dict[str, int]:
    return {
        "attempts": 0,
        "accepted_attempts": 0,
        "protocol_correction_attempts": 0,
        "schema_retry_count": 0,
        "protocol_rejections": 0,
    }


def _empty_stats() -> dict[str, object]:
    return {
        "attempts": 0,
        "accepted_attempts": 0,
        "protocol_correction_attempts": 0,
        "schema_retry_count": 0,
        "protocol_rejections": 0,
        "native": _empty_mode_stats(),
        "fallback": _empty_mode_stats(),
    }


def _accumulate(stats: dict[str, object], record: dict[str, object]) -> None:
    mode = str(record.get("structured_output_mode", "") or "").strip()
    bucket_name = "native" if mode in _NATIVE_MODES else "fallback"
    bucket = stats.get(bucket_name)
    if not isinstance(bucket, dict):
        return

    accepted = bool(record.get("accepted", False))
    correction = str(record.get("attempt_kind", "")) == "protocol-correction"
    schema_retries = max(0, _int(record.get("schema_retry_count", 0)))
    rejected = (
        not accepted
        and str(record.get("failure_classification", ""))
        in {"role-protocol-failure", "role-protocol-exhausted"}
    )

    for target in (stats, bucket):
        target["attempts"] = _int(target.get("attempts", 0)) + 1
        if accepted:
            target["accepted_attempts"] = _int(
                target.get("accepted_attempts", 0)
            ) + 1
        if correction:
            target["protocol_correction_attempts"] = _int(
                target.get("protocol_correction_attempts", 0)
            ) + 1
        target["schema_retry_count"] = _int(
            target.get("schema_retry_count", 0)
        ) + schema_retries
        if rejected:
            target["protocol_rejections"] = _int(
                target.get("protocol_rejections", 0)
            ) + 1


def _ux_context_active(context: dict[str, object]) -> bool:
    return bool(
        str(context.get("ux_context_fingerprint", "") or "").strip()
        or isinstance(context.get("ux_context"), dict)
    )


def _has_mechanical_ux_evidence(current: Path, role: str) -> bool:
    if role == "verifier":
        result = _read_json(current / "verification-result.json")
        findings = result.get("ux_findings", [])
        return isinstance(findings, list) and bool(findings)

    sidecar = _read_json(current / f"structured-ux-{role}.json")
    constraints = sidecar.get("constraints_addressed", [])
    return isinstance(constraints, list) and bool(constraints)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize content-free AutoDev Structured Output rollout metrics."
    )
    parser.add_argument("repo", nargs="?", default=".")
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(Path(args.repo)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

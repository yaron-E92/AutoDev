from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Callable

from automation import run_manifest


FAILURE_REPAIR_BUDGET_EXHAUSTED = "repair-budget-exhausted"
ROOT_FAILURE_CLASSIFICATION = "code-repairable"
FORMULA_VERSION = 1
POLICY_ENV = "SEMANTIC_REPAIR_BUDGET_POLICY"
FIXED_LIMIT_ENV = "MAX_SEMANTIC_REPAIR_ATTEMPTS"
ADAPTIVE_MIN_ENV = "SEMANTIC_REPAIR_ADAPTIVE_MIN_ATTEMPTS"
ADAPTIVE_MAX_ENV = "SEMANTIC_REPAIR_ADAPTIVE_MAX_ATTEMPTS"
ADAPTIVE_BASE_ENV = "SEMANTIC_REPAIR_ADAPTIVE_BASE_ATTEMPTS"
LINES_PER_ATTEMPT_ENV = "SEMANTIC_REPAIR_LINES_PER_ATTEMPT"
DEFAULT_ADAPTIVE_MIN = 1
DEFAULT_ADAPTIVE_MAX = 5
DEFAULT_ADAPTIVE_BASE = 1
DEFAULT_LINES_PER_ATTEMPT = 200

_GENERATED_PREFIXES = (
    ".git/",
    ".autodev-run/",
    "bin/",
    "obj/",
    "node_modules/",
    "dist/",
    "build/",
    "coverage/",
    ".vs/",
    ".idea/",
    ".vscode/",
    ".venv/",
    "venv/",
    "__pycache__/",
)
_BINARY_SUFFIXES = {
    ".7z",
    ".a",
    ".avi",
    ".bin",
    ".bmp",
    ".class",
    ".dll",
    ".dylib",
    ".eot",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".lib",
    ".mov",
    ".mp3",
    ".mp4",
    ".o",
    ".obj",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tar",
    ".ttf",
    ".wav",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}


class SemanticRepairBudgetError(ValueError):
    pass


def validate_config(*, fixed_default: int) -> None:
    policy = _policy()
    _nonnegative_int(FIXED_LIMIT_ENV, fixed_default)
    if policy == "fixed":
        return
    minimum = _nonnegative_int(ADAPTIVE_MIN_ENV, DEFAULT_ADAPTIVE_MIN)
    maximum = _nonnegative_int(ADAPTIVE_MAX_ENV, DEFAULT_ADAPTIVE_MAX)
    base = _nonnegative_int(ADAPTIVE_BASE_ENV, DEFAULT_ADAPTIVE_BASE)
    lines = _positive_int(LINES_PER_ATTEMPT_ENV, DEFAULT_LINES_PER_ATTEMPT)
    if minimum > maximum:
        raise SemanticRepairBudgetError(
            f"{ADAPTIVE_MIN_ENV} must be less than or equal to {ADAPTIVE_MAX_ENV}"
        )
    if base > maximum:
        # This is allowed mathematically, but almost certainly a configuration mistake.
        raise SemanticRepairBudgetError(
            f"{ADAPTIVE_BASE_ENV} must be less than or equal to {ADAPTIVE_MAX_ENV}"
        )
    if lines <= 0:
        raise SemanticRepairBudgetError(f"{LINES_PER_ATTEMPT_ENV} must be positive")


def resolve_budget(
    repo: Path,
    state: dict[str, object],
    *,
    attempt: int,
    fixed_default: int,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    """Resolve a bounded semantic repair budget.

    Once computed, a run keeps its persisted policy and formula inputs so resume
    is deterministic. A larger explicit fixed limit, or a larger adaptive cap,
    may only increase the persisted limit; configuration changes never reduce a
    budget below either its previous value or attempts already consumed.
    """

    validate_config(fixed_default=fixed_default)
    existing = state.get("SemanticRepairBudget")
    if isinstance(existing, dict) and existing.get("formula_version") == FORMULA_VERSION:
        return _resume_budget(existing, attempt=attempt, fixed_default=fixed_default)

    policy = _policy()
    if policy == "fixed":
        configured = _nonnegative_int(FIXED_LIMIT_ENV, fixed_default)
        return {
            "policy": "fixed",
            "formula_version": FORMULA_VERSION,
            "configured_limit": configured,
            "effective_limit": max(configured, attempt),
            "attempts_consumed": attempt,
            "inputs": {},
        }

    minimum = _nonnegative_int(ADAPTIVE_MIN_ENV, DEFAULT_ADAPTIVE_MIN)
    maximum = _nonnegative_int(ADAPTIVE_MAX_ENV, DEFAULT_ADAPTIVE_MAX)
    base = _nonnegative_int(ADAPTIVE_BASE_ENV, DEFAULT_ADAPTIVE_BASE)
    lines_per_attempt = _positive_int(LINES_PER_ATTEMPT_ENV, DEFAULT_LINES_PER_ATTEMPT)
    metrics = change_metrics(repo, state, runner=runner)
    weighted = int(metrics["weighted_changed_lines"])
    raw_attempts = base + math.ceil(weighted / lines_per_attempt)
    computed = min(maximum, max(minimum, raw_attempts))
    return {
        "policy": "adaptive",
        "formula_version": FORMULA_VERSION,
        "base_attempts": base,
        "min_attempts": minimum,
        "max_attempts": maximum,
        "lines_per_attempt": lines_per_attempt,
        "raw_attempts": raw_attempts,
        "effective_limit": max(computed, attempt),
        "attempts_consumed": attempt,
        "inputs": metrics,
    }


def _resume_budget(
    existing: dict[str, object],
    *,
    attempt: int,
    fixed_default: int,
) -> dict[str, object]:
    budget = json.loads(json.dumps(existing))
    previous = int(budget.get("effective_limit", 0) or 0)
    effective = max(previous, attempt)

    explicit_fixed = _nonnegative_int(FIXED_LIMIT_ENV, fixed_default)
    if explicit_fixed > effective:
        effective = explicit_fixed
        budget["manual_limit_increase"] = explicit_fixed

    if str(budget.get("policy", "")) == "adaptive":
        old_cap = int(budget.get("max_attempts", DEFAULT_ADAPTIVE_MAX) or 0)
        new_cap = _nonnegative_int(ADAPTIVE_MAX_ENV, old_cap)
        if new_cap > old_cap:
            raw_attempts = int(budget.get("raw_attempts", 0) or 0)
            minimum = int(budget.get("min_attempts", DEFAULT_ADAPTIVE_MIN) or 0)
            recomputed = min(new_cap, max(minimum, raw_attempts))
            if recomputed > effective:
                effective = recomputed
            budget["max_attempts"] = new_cap
            budget["adaptive_cap_increased_from"] = old_cap

    budget["effective_limit"] = effective
    budget["attempts_consumed"] = attempt
    if attempt > int(budget.get("max_attempts", effective) or effective) and budget.get("policy") == "adaptive":
        budget["cap_exceeded_by_consumed_attempts"] = True
    return budget


def change_metrics(
    repo: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    changes = state.get("VerifiedChanges", [])
    changes = changes if isinstance(changes, list) else []
    base_sha = str(state.get("VerifiedParentSha", "") or state.get("BaseSha", "")).strip()

    additions = 0
    deletions = 0
    weighted_total = 0.0
    eligible_paths: list[str] = []
    skipped_generated: list[str] = []
    skipped_binary: list[str] = []

    for item in changes:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("path", item.get("Path", ""))).replace("\\", "/").removeprefix("./")
        status = str(item.get("status", item.get("Status", ""))).casefold()
        if not relative:
            continue
        if _generated(relative):
            skipped_generated.append(relative)
            continue
        if Path(relative).suffix.casefold() in _BINARY_SUFFIXES:
            skipped_binary.append(relative)
            continue

        added, deleted, binary = _changed_lines(repo, base_sha, relative, status, runner=runner)
        if binary:
            skipped_binary.append(relative)
            continue
        eligible_paths.append(relative)
        additions += added
        deletions += deleted
        weighted_total += (added + deleted) * _path_weight(relative)

    return {
        "changed_file_count": len(eligible_paths),
        "eligible_paths": sorted(eligible_paths),
        "skipped_generated_paths": sorted(skipped_generated),
        "skipped_binary_paths": sorted(skipped_binary),
        "added_lines": additions,
        "deleted_lines": deletions,
        "weighted_changed_lines": math.ceil(weighted_total),
    }


def _changed_lines(
    repo: Path,
    base_sha: str,
    relative: str,
    status: str,
    *,
    runner: Callable[..., object],
) -> tuple[int, int, bool]:
    if base_sha:
        try:
            completed = runner(
                ["git", "diff", "--numstat", "--no-renames", "-z", base_sha, "--", relative],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError:
            completed = None
        if completed is not None and int(getattr(completed, "returncode", 1)) == 0:
            raw = getattr(completed, "stdout", b"") or b""
            if isinstance(raw, str):
                raw = raw.encode("utf-8", errors="surrogateescape")
            record = raw.split(b"\0", 1)[0]
            fields = record.split(b"\t", 2)
            if len(fields) >= 2:
                if fields[0] == b"-" or fields[1] == b"-":
                    return 0, 0, True
                try:
                    return int(fields[0]), int(fields[1]), False
                except ValueError:
                    pass

    path = repo / relative
    if status == "deleted" or not path.is_file():
        return 0, 0, False
    try:
        data = path.read_bytes()
    except OSError:
        return 0, 0, False
    if b"\0" in data[:8192]:
        return 0, 0, True
    return _line_count(data), 0, False


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _generated(relative: str) -> bool:
    normalized = relative.casefold().replace("\\", "/").removeprefix("./")
    return any(
        normalized.startswith(prefix) or f"/{prefix}" in f"/{normalized}"
        for prefix in _GENERATED_PREFIXES
    )


def _path_weight(relative: str) -> float:
    normalized = relative.casefold().replace("\\", "/")
    name = Path(normalized).name
    if (
        normalized.startswith("tests/")
        or "/tests/" in f"/{normalized}"
        or name.startswith("test_")
        or name.endswith("tests.cs")
        or name.endswith("test.cs")
        or name.endswith(".spec.ts")
        or name.endswith(".test.ts")
        or name.endswith(".spec.js")
        or name.endswith(".test.js")
    ):
        return 0.5
    if Path(normalized).suffix in {".md", ".rst", ".txt"}:
        return 0.25
    return 1.0


def failure_details(
    result: dict[str, object],
    budget: dict[str, object],
    *,
    attempt: int,
    verification_result: Path,
    repair_artifact: Path,
    verified_source_identity: str,
) -> dict[str, object]:
    requirements = []
    for item in result.get("requirements", []) if isinstance(result.get("requirements"), list) else []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", ""))
        if status not in {"missing", "uncertain"}:
            continue
        requirements.append(
            {
                "criterion": str(item.get("criterion", "")),
                "status": status,
                "evidence": [str(value) for value in item.get("evidence", [])]
                if isinstance(item.get("evidence"), list)
                else [],
            }
        )

    findings = []
    for item in result.get("findings", []) if isinstance(result.get("findings"), list) else []:
        if not isinstance(item, dict) or str(item.get("severity", "")) != "blocking":
            continue
        findings.append(
            {
                "severity": "blocking",
                "message": str(item.get("message", "")),
                "path": str(item.get("path", "")),
            }
        )

    fingerprint_source = {
        "result": result,
        "verified_source_identity": verified_source_identity,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_source,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8", errors="replace")
    ).hexdigest()
    return {
        "kind": "semantic",
        "classification": FAILURE_REPAIR_BUDGET_EXHAUSTED,
        "root_classification": ROOT_FAILURE_CLASSIFICATION,
        "attempted_repairs": attempt,
        "maximum_repairs": int(budget.get("effective_limit", 0) or 0),
        "repair_brief": str(result.get("repair_brief", "")),
        "requirements": requirements,
        "findings": findings,
        "verification_result": str(verification_result),
        "repair_artifact": str(repair_artifact),
        "verified_source_identity": verified_source_identity,
        "failure_fingerprint": fingerprint,
        "budget": budget,
    }


def concise_failure_reason(details: dict[str, object]) -> str:
    attempted = int(details.get("attempted_repairs", 0) or 0)
    maximum = int(details.get("maximum_repairs", 0) or 0)
    brief = " ".join(str(details.get("repair_brief", "")).split())
    return (
        f"semantic repair budget exhausted after {attempted}/{maximum} automatic repairs"
        + (f"; final repair: {brief}" if brief else "")
    )[:1000]


def human_failure_summary(details: dict[str, object], fallback: str = "") -> str:
    if not details:
        return fallback
    lines = [
        f"Semantic repair budget exhausted: {details.get('attempted_repairs', 0)}/{details.get('maximum_repairs', 0)} automatic repairs consumed.",
    ]
    brief = str(details.get("repair_brief", "")).strip()
    if brief:
        lines.extend(["", "Final repair brief:", brief])
    requirements = details.get("requirements", [])
    if isinstance(requirements, list) and requirements:
        lines.extend(["", "Unmet/uncertain requirements:"])
        for item in requirements:
            if isinstance(item, dict):
                lines.append(
                    f"- [{item.get('status', '')}] {item.get('criterion', '')}"
                )
    findings = details.get("findings", [])
    if isinstance(findings, list) and findings:
        lines.extend(["", "Blocking findings:"])
        for item in findings:
            if isinstance(item, dict):
                path = str(item.get("path", "")).strip()
                prefix = f"{path}: " if path else ""
                lines.append(f"- {prefix}{item.get('message', '')}")
    lines.extend(
        [
            "",
            f"Verification result: {details.get('verification_result', '')}",
            f"Verified source identity: {details.get('verified_source_identity', '')}",
            f"Failure fingerprint: {details.get('failure_fingerprint', '')}",
            "Root classification: code-repairable (automatic repair budget exhausted).",
        ]
    )
    return "\n".join(lines)


def persist_budget(repo: Path, state: dict[str, object], budget: dict[str, object]) -> None:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state["SemanticRepairBudget"] = budget
    _write_json(current / "state.json", state)
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError:
        return
    semantic = manifest.setdefault("semantic_verification", {})
    if isinstance(semantic, dict):
        semantic["repair_budget"] = budget
    run_manifest.save_manifest(manifest_path, manifest)


def persist_failure(repo: Path, state: dict[str, object], details: dict[str, object]) -> None:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state["LastSemanticFailureDetails"] = details
    _write_json(current / "state.json", state)
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError:
        return
    manifest["failure"] = {
        "classification": FAILURE_REPAIR_BUDGET_EXHAUSTED,
        "root_classification": ROOT_FAILURE_CLASSIFICATION,
        "reason": concise_failure_reason(details),
        "stage": "semantic-verified",
        "fingerprint": str(details.get("failure_fingerprint", "")),
        "details": details,
        "recorded_at": run_manifest.utc_now(),
    }
    semantic = manifest.setdefault("semantic_verification", {})
    if isinstance(semantic, dict):
        semantic["repair_budget"] = details.get("budget", {})
    run_manifest.save_manifest(manifest_path, manifest)


def clear_failure_state(repo: Path, state: dict[str, object]) -> None:
    if "LastSemanticFailureDetails" not in state:
        return
    state.pop("LastSemanticFailureDetails", None)
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    _write_json(current / "state.json", state)


def install_run_manifest_hooks() -> None:
    if getattr(run_manifest.record_failure, "_autodev_semantic_budget_hook", False):
        return
    original = run_manifest.record_failure

    def record_failure(path: Path, *, classification: str, reason: str, stage: str = ""):
        rich: dict[str, object] = {}
        try:
            before = run_manifest.load_manifest(path).get("failure", {})
            if (
                isinstance(before, dict)
                and before.get("classification") == FAILURE_REPAIR_BUDGET_EXHAUSTED
                and classification == FAILURE_REPAIR_BUDGET_EXHAUSTED
            ):
                rich = {
                    key: before[key]
                    for key in ("root_classification", "fingerprint", "details")
                    if key in before
                }
        except run_manifest.ManifestError:
            pass
        manifest = original(
            path,
            classification=classification,
            reason=reason,
            stage=stage,
        )
        if rich:
            failure = manifest.get("failure", {})
            if isinstance(failure, dict):
                failure.update(rich)
            run_manifest.save_manifest(path, manifest)
        return manifest

    setattr(record_failure, "_autodev_semantic_budget_hook", True)
    run_manifest.record_failure = record_failure


def maybe_reopen_exhausted_budget(repo: Path, *, fixed_default: int = 2) -> bool:
    repo = repo.expanduser().resolve()
    current = repo / ".autodev-run" / "current"
    manifest_path = current / run_manifest.MANIFEST_NAME
    state = _read_json(current / "state.json")
    if not isinstance(state, dict) or not manifest_path.is_file():
        return False
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError:
        return False
    failure = manifest.get("failure", {})
    if not isinstance(failure, dict) or failure.get("classification") != FAILURE_REPAIR_BUDGET_EXHAUSTED:
        return False
    details = failure.get("details", {})
    if not isinstance(details, dict):
        return False
    previous = state.get("SemanticRepairBudget", details.get("budget", {}))
    if not isinstance(previous, dict):
        return False
    attempt = int(details.get("attempted_repairs", previous.get("attempts_consumed", 0)) or 0)
    old_limit = int(previous.get("effective_limit", 0) or 0)
    state["SemanticRepairBudget"] = previous
    raised = resolve_budget(
        repo,
        state,
        attempt=attempt,
        fixed_default=fixed_default,
    )
    new_limit = int(raised.get("effective_limit", 0) or 0)
    if new_limit <= old_limit:
        return False

    state["SemanticRepairBudget"] = raised
    state["Status"] = "SemanticRepairRequired"
    state.pop("LastSemanticFailureDetails", None)
    _write_json(current / "state.json", state)

    semantic = manifest.setdefault("semantic_verification", {})
    if isinstance(semantic, dict):
        semantic["repair_budget"] = raised
    stages = manifest.setdefault("stages", {})
    stage = stages.get("semantic-verified", {}) if isinstance(stages, dict) else {}
    if not isinstance(stage, dict):
        stage = {}
    stage_details = stage.get("details", {})
    stage_details = dict(stage_details) if isinstance(stage_details, dict) else {}
    stage_details.update(
        {
            "attempt": attempt,
            "repair_kind": "semantic",
            "reason": "semantic repair budget increased; resume at pending semantic repair",
            "failure_classification": ROOT_FAILURE_CLASSIFICATION,
            "artifact": str(details.get("repair_artifact", "")),
            "semantic_repair_budget": raised,
        }
    )
    stage.update(
        {
            "status": "repair-required",
            "updated_at": run_manifest.utc_now(),
            "details": stage_details,
        }
    )
    if isinstance(stages, dict):
        stages["semantic-verified"] = stage
    manifest["failure"] = {}
    manifest["current_stage"] = "semantic-verified"
    run_manifest.save_manifest(manifest_path, manifest)
    return True


def install_opencode_resume_hooks() -> None:
    from automation import opencode_resume

    if getattr(opencode_resume.resume, "_autodev_semantic_budget_hook", False):
        return
    original_resume = opencode_resume.resume
    original_status = opencode_resume.status_text

    def resume(repo: Path, mappings: dict[str, dict[str, str]], **kwargs):
        maybe_reopen_exhausted_budget(Path(repo))
        payload = original_resume(repo, mappings, **kwargs)
        _append_resume_metadata(Path(repo), payload)
        return payload

    def status_text(repo: Path, mappings: dict[str, dict[str, str]], **kwargs):
        text = original_status(repo, mappings, **kwargs).rstrip()
        extra = _status_metadata(Path(repo))
        return text + ("\n" + extra if extra else "") + "\n"

    setattr(resume, "_autodev_semantic_budget_hook", True)
    setattr(status_text, "_autodev_semantic_budget_hook", True)
    opencode_resume.resume = resume
    opencode_resume.status_text = status_text


def _append_resume_metadata(repo: Path, payload: dict[str, object]) -> None:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state = _read_json(current / "state.json")
    if isinstance(state, dict) and isinstance(state.get("SemanticRepairBudget"), dict):
        payload["semantic_repair_budget"] = state["SemanticRepairBudget"]
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        failure = run_manifest.load_manifest(manifest_path).get("failure", {})
    except run_manifest.ManifestError:
        return
    if isinstance(failure, dict) and isinstance(failure.get("details"), dict):
        payload["semantic_failure"] = failure["details"]


def _status_metadata(repo: Path) -> str:
    current = repo.expanduser().resolve() / ".autodev-run" / "current"
    state = _read_json(current / "state.json")
    budget = state.get("SemanticRepairBudget", {}) if isinstance(state, dict) else {}
    lines: list[str] = []
    if isinstance(budget, dict) and budget:
        lines.append(
            "Semantic repair budget: "
            f"policy={budget.get('policy', '')} "
            f"consumed={budget.get('attempts_consumed', 0)} "
            f"limit={budget.get('effective_limit', 0)}"
        )
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return "\n".join(lines)
    try:
        failure = run_manifest.load_manifest(manifest_path).get("failure", {})
    except run_manifest.ManifestError:
        return "\n".join(lines)
    details = failure.get("details", {}) if isinstance(failure, dict) else {}
    if not isinstance(details, dict) or not details:
        return "\n".join(lines)
    lines.append("Semantic budget failure details:")
    lines.append(f"  attempted/max: {details.get('attempted_repairs', 0)}/{details.get('maximum_repairs', 0)}")
    lines.append(f"  repair brief: {details.get('repair_brief', '')}")
    lines.append(f"  verification result: {details.get('verification_result', '')}")
    lines.append(f"  verified source identity: {details.get('verified_source_identity', '')}")
    lines.append(f"  failure fingerprint: {details.get('failure_fingerprint', '')}")
    requirements = details.get("requirements", [])
    if isinstance(requirements, list):
        for item in requirements:
            if isinstance(item, dict):
                lines.append(f"  requirement [{item.get('status', '')}]: {item.get('criterion', '')}")
    findings = details.get("findings", [])
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict):
                path = str(item.get("path", ""))
                lines.append(f"  blocking finding{f' ({path})' if path else ''}: {item.get('message', '')}")
    return "\n".join(lines)


def _policy() -> str:
    value = os.environ.get(POLICY_ENV, "fixed").strip().casefold() or "fixed"
    if value not in {"fixed", "adaptive"}:
        raise SemanticRepairBudgetError(f"{POLICY_ENV} must be fixed or adaptive")
    return value


def _nonnegative_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SemanticRepairBudgetError(f"{name} must be an integer") from exc
    if value < 0:
        raise SemanticRepairBudgetError(f"{name} must be zero or greater")
    return value


def _positive_int(name: str, default: int) -> int:
    value = _nonnegative_int(name, default)
    if value <= 0:
        raise SemanticRepairBudgetError(f"{name} must be positive")
    return value


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

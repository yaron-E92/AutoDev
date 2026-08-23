from __future__ import annotations

import json
import math
import os
import subprocess
from pathlib import Path
from typing import Callable

from automation.repair_budget_contract import (
    ADAPTIVE_BASE_ENV,
    ADAPTIVE_MAX_ENV,
    ADAPTIVE_MIN_ENV,
    DEFAULT_ADAPTIVE_BASE,
    DEFAULT_ADAPTIVE_MAX,
    DEFAULT_ADAPTIVE_MIN,
    DEFAULT_LINES_PER_ATTEMPT,
    FIXED_LIMIT_ENV,
    FORMULA_VERSION,
    LINES_PER_ATTEMPT_ENV,
    POLICY_ENV,
    SemanticRepairBudgetError,
)
from automation.repair_budget_metrics import (
    change_metrics,
)


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
    """Resume a persisted budget without treating inherited defaults as consent."""

    budget = json.loads(json.dumps(existing))
    previous = int(budget.get("effective_limit", 0) or 0)
    effective = max(previous, attempt)

    observed = int(
        budget.get(
            "fixed_limit_observed",
            budget.get("configured_limit", fixed_default),
        )
        or 0
    )
    current_fixed = _nonnegative_int(FIXED_LIMIT_ENV, observed)
    budget.setdefault("fixed_limit_observed", observed)
    if current_fixed > observed:
        budget["fixed_limit_observed"] = current_fixed
        if current_fixed > effective:
            effective = current_fixed
            budget["manual_limit_increase"] = current_fixed

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
    if (
        budget.get("policy") == "adaptive"
        and attempt > int(budget.get("max_attempts", effective) or effective)
    ):
        budget["cap_exceeded_by_consumed_attempts"] = True
    return budget


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

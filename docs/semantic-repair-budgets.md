# Semantic repair budgets

Semantic repair is bounded. A verifier may return `repair`, but AutoDev only performs the configured number of automatic semantic repairs before stopping for human action.

## Fixed policy

`fixed` is the default:

```text
SEMANTIC_REPAIR_BUDGET_POLICY=fixed
MAX_SEMANTIC_REPAIR_ATTEMPTS=2
```

When the budget is exhausted, AutoDev preserves the final repair brief, requirement/finding evidence, verified source identity, verification-result path, and stable failure fingerprint. The terminal classification remains `repair-budget-exhausted` rather than discarding the underlying semantic diagnosis.

## Adaptive policy

Adaptive budgeting is opt-in. It derives a bounded effective limit from verified issue-scoped changed lines and configured minimum/base/maximum values. Generated/build/cache paths and binary changes are excluded; tests and documentation are weighted below source code.

The computed inputs, formula version, caps, and effective limit are persisted. Resume reuses the persisted budget instead of silently recomputing it from a changed environment.

## Raising an exhausted budget

An operator may explicitly increase a blocked run's limit and resume:

```text
MAX_SEMANTIC_REPAIR_ATTEMPTS=4 autodev resume
```

Existing attempts remain counted. Lower values never shrink a persisted budget. For adaptive policy, increasing the configured adaptive cap may raise a cap-limited run.

## Durable evidence

Canonical semantic output remains:

```text
.autodev-run/current/verification-result.json
```

When repair is required AutoDev prepares:

```text
.autodev-run/current/verification-repair.md
```

That is a generated run artifact, not a prompt-template source file.

## Safety

Repair-budget changes do not bypass deterministic/platform verification, reset counters, approve verifier failures, or create unlimited retry loops. A repair modifies source identity and therefore re-enters the required verification boundaries before semantic review continues.

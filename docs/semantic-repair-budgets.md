# Semantic repair budgets

AutoDev keeps semantic verification bounded. A semantic verifier may return `repair`, but the workflow will only run a configured number of automatic semantic repairs before blocking for human intervention.

## Fixed policy

`fixed` remains the default policy and preserves the existing `MAX_SEMANTIC_REPAIR_ATTEMPTS` behavior.

```bash
export SEMANTIC_REPAIR_BUDGET_POLICY=fixed
export MAX_SEMANTIC_REPAIR_ATTEMPTS=2
```

The installed OpenCode bridge continues to supply its existing default of `2` when the variable is not explicitly set. Other callers retain their existing workflow-stage default.

When the final verifier still returns `repair` after the budget is consumed, AutoDev now records the final repair brief, unmet/uncertain requirements, blocking findings and paths, the canonical verification-result path, verified source identity, and a stable failure fingerprint. The terminal classification is `repair-budget-exhausted`; the root semantic failure remains `code-repairable`.

## Adaptive policy

Adaptive budgeting is opt-in:

```bash
export SEMANTIC_REPAIR_BUDGET_POLICY=adaptive
export SEMANTIC_REPAIR_ADAPTIVE_BASE_ATTEMPTS=1
export SEMANTIC_REPAIR_ADAPTIVE_MIN_ATTEMPTS=1
export SEMANTIC_REPAIR_ADAPTIVE_MAX_ATTEMPTS=5
export SEMANTIC_REPAIR_LINES_PER_ATTEMPT=200
```

Formula version 1 is:

```text
clamp(min_attempts, max_attempts,
      base_attempts + ceil(weighted_changed_lines / lines_per_attempt))
```

The inputs come from `VerifiedChanges`, so the budget is based on the same verified issue-scoped source identity used by semantic verification. Git-excluded files are already absent from that change set. AutoDev additionally excludes generated/build/cache paths such as `.autodev-run`, `bin`, `obj`, `node_modules`, `dist`, `build`, and `coverage`, and excludes binary changes.

Source changes have weight `1.0`, test changes `0.5`, and Markdown/RST/text documentation changes `0.25`. The resulting inputs, formula version, policy, caps, and effective limit are persisted in `state.json` and in `run-manifest.json` under `semantic_verification.repair_budget`.

The budget is computed once for a run and then reused. Resume does not silently recompute it from a changed environment, so a restart remains deterministic.

## Exhaustion and diagnosis

A blocked semantic run retains structured failure evidence in the run manifest and state. `/autodev-status` reports the budget plus the final semantic diagnosis, including the repair brief, relevant requirements/findings, verification result, source identity, and fingerprint. The GitHub blocked comment uses the same retained diagnosis rather than replacing it with only “repair-attempt limit exhausted.”

The final `verification-result.json` remains canonical:

```text
.autodev-run/current/verification-result.json
```

AutoDev also prepares the corresponding repair prompt even when the current automatic budget is exhausted:

```text
.autodev-run/current/verification-repair.md
```

That makes a later explicitly authorized resume possible without rerunning preparation, reading, planning, or implementation.

## Raising a blocked budget

A budget is never reduced below attempts already consumed. To authorize additional semantic repairs for a blocked run, raise the existing fixed limit and resume:

```bash
MAX_SEMANTIC_REPAIR_ATTEMPTS=4 python3 .opencode/autodev.py coordinate --resume
```

If the previous effective limit was `2`, AutoDev reopens the existing `semantic-verified` checkpoint as `repair-required` and continues with the semantic fixer. Existing repair counters are retained.

For an adaptive run, increasing `SEMANTIC_REPAIR_ADAPTIVE_MAX_ATTEMPTS` may raise the limit when the previous adaptive result was cap-limited. `MAX_SEMANTIC_REPAIR_ATTEMPTS` can also be raised above the persisted effective limit as an explicit manual increase.

Lower values never shrink a persisted budget.

## Safety

Semantic repair remains bounded. This feature does not introduce unlimited retry loops, reset counters, bypass the verifier, or silently restart a run. An exhausted budget is a policy boundary, not proof that the underlying defect is intrinsically unrepairable.

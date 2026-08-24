# Semantic verification gate

AutoDev runs semantic verification as a deterministic workflow stage after required deterministic/platform verification and before final PR/CI progression.

## Order

```text
implementation
  -> local deterministic verification
  -> required Windows/platform verification, when configured
  -> semantic verifier
       -> pass: continue
       -> blocked: stop with durable evidence
       -> repair: targeted fixer boundary
            -> deterministic verification again
            -> semantic verification again
  -> PR / CI
```

Any repair changes source identity and therefore requires the relevant verification boundaries again.

## Verifier contract

The verifier receives bounded issue-relevant evidence and writes strict JSON. Allowed verdicts are:

```text
pass
repair
blocked
```

Requirement statuses are `met`, `missing`, or `uncertain`. Finding severities are `blocking` or `warning`.

A `pass` result is rejected when a requirement is not `met` or a blocking finding exists. A `repair` result requires a targeted non-empty repair brief. Malformed output never defaults to pass.

The canonical verifier template is:

```text
promptTemplates/semantic-verifier.md
```

The canonical repair template is:

```text
promptTemplates/semantic-repair.md
```

Template placeholders use `{~{Name}~}` syntax.

## Durable artifacts

Semantic state is stored beneath `.autodev-run/current`. Important artifacts include the verifier input/result, deterministic evidence, repair budget state, and when repair is required:

```text
.autodev-run/current/verification-repair.md
```

That file is a generated run artifact for the Fixer. It is not a prompt template.

Repair-budget ownership lives in the `repair_budget_*` modules, so resume preserves the effective budget and final diagnosis rather than resetting a blocked run accidentally.

## Resume

Interrupted semantic work resumes through the normal durable coordinator:

```text
autodev resume
```

The coordinator validates source/artifact identity before selecting the next verifier, fixer, platform-verification, or PR/CI boundary.

## Privacy

The Verifier is a normal AutoDev role for privacy purposes. Its route must be authorized before model execution. Current role/runtime privacy policy and grants apply exactly as they do to Reader, Planner, Implementer, and Fixer.

See `docs/privacy.md`, `docs/semantic-repair-budgets.md`, and `docs/windows-verification.md`.

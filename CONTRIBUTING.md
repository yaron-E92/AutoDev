# Contributing to AutoDev

AutoDev's supported product surface is the `autodev` CLI plus the maintained OpenCode, scheduler, Windows-verification, CI, and release integrations. Do not reintroduce source-checkout-only launchers or compatibility implementations after they have been retired.

## Development checks

Before opening or updating a PR, run:

```text
python -m compileall -q automation area_reader tests
python -m unittest discover -s tests -v
```

These are **source-development checks**, not end-user `autodev` subcommands. The public CLI deliberately keeps contributor-only helpers out of normal command discovery; `autodev --help` points contributors back to these checks without advertising internal workflow commands.

Also run the relevant platform or workflow checks for files you touched. CI covers the supported Linux/Windows Python matrix, canonical CLI smoke tests, PowerShell/Bash syntax, workflow lint and immutable external action refs, release reproducibility, version intent, repository hygiene, and exact-source Windows verification.

## Architecture rules

`tests/test_python_architecture.py` is a permanent guardrail. In production Python under `automation/` and `area_reader/`:

- keep responsibility modules below the configured giant-module threshold;
- keep the top-level local import graph acyclic;
- depend on owning responsibility modules rather than aggregate compatibility facades;
- do not restore paths explicitly listed as removed;
- do not add temporary issue-migration workflows/scripts to the finished tree;
- do not commit `*.chunk*.txt` artifacts.

When a migration leaves an old path unused, delete it instead of keeping an indefinite shim unless a current supported entrypoint demonstrably requires it.

## Workflow behavior

Python owns deterministic sequencing, durable state, resume decisions, verification boundaries, repair budgets, PR/CI progression, and terminal outcomes. Model runtimes should receive bounded role inputs and must not become owners of workflow transitions.

The canonical command for a normal issue run is:

```text
autodev issue-to-pr 123
```

`autodev coordinate --arguments 123` remains the advanced/integration spelling over the same coordinator, but it is not the normal user-facing entrypoint.

Resume with:

```text
autodev resume
```

OpenCode is an optional frontend over the same workflow. Its model mapping remains in `opencode.json` / `opencode.jsonc`.

## Prompt templates

Maintained workflow templates live in `promptTemplates/`. Template placeholders use the canonical delimiter:

```text
{~{IssueText}~}
{~{Plan}~}
```

Use the canonical placeholder syntax consistently. Current semantic templates are:

```text
promptTemplates/semantic-verifier.md
promptTemplates/semantic-repair.md
```

The durable repair artifact `.autodev-run/current/verification-repair.md` is run state, not a prompt-template file.

## Tests

Delete tests that only exercise deleted compatibility behavior. Preserve or retarget tests that protect a still-supported contract. A cleanup PR should prove both that the obsolete path is gone and that current behavior remains covered.

Prefer focused tests for the owning module plus the full unit suite before finalizing broad refactors.

## Documentation

Document only current supported entrypoints. Git history is the archive for retired commands and architecture. If code is deleted, remove instructions that tell users to invoke it.

## Version intent

PRs must state release intent using the repository's version-intent convention. Behavior-preserving cleanup such as dead-code removal uses:

```text
+semver: none
```

Use a release-advancing intent only when the change actually warrants one.

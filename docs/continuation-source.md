# Continuing from existing implementation work

`autodev issue-to-pr ... --continue-from <ref>` and `autodev resume --continue-from <ref>` adopt existing implementation bytes without changing the repository's configured development strategy or PR target.

This is deliberately different from the legacy `BASE_BRANCH` override:

- `--continue-from` answers **which implementation commit should AutoDev start from?**
- `BASE_BRANCH` answers **which development/base branch policy should this run use?**

For a Git-Flow repository, this command:

```text
autodev issue-to-pr 182 --continue-from feature/existing-work
```

means:

```text
continuation source     feature/existing-work -> resolved immutable SHA
implementation baseline that resolved SHA
development base        develop
PR target               develop
```

It does not retarget the PR to `feature/existing-work`.

## Immutable resolution

AutoDev resolves the supplied local branch, tag, or commit to a commit SHA once. The requested ref and resolved SHA are persisted in durable run state and the run manifest.

If the branch or tag moves later, resume continues from the persisted SHA instead of silently following the moved ref.

The requested object must already be resolvable in the local repository. Fetch a remote branch/tag first when necessary, or pass a resolvable commit SHA directly.

## Git-Flow ancestry safety

A continuation source must be descended from the configured development-base commit. If it is unrelated to or behind a different history, AutoDev refuses the adoption rather than changing the PR base implicitly.

Rebase or cherry-pick the recovered work onto the configured integration line first when necessary.

This keeps continuation-source semantics independent from repository development policy.

## Worktree behavior

Adopting a continuation source requires a clean worktree. AutoDev checks out the resolved continuation commit in detached-HEAD form so the local implementation bytes exactly match the persisted immutable identity.

AutoDev later creates/uses its own shipment branch for generated commits and PR creation. The existing source branch is not treated as an AutoDev-owned PR branch.

## Starting a new run

For a new `issue-to-pr` run, AutoDev:

1. resolves the normal repository development policy and integration base;
2. resolves `--continue-from` once to an immutable SHA;
3. verifies that SHA is descended from the configured development base;
4. checks out that SHA and records the workspace as the implementation baseline;
5. persists the requested ref and resolved SHA;
6. runs the normal Reader -> Synthesizer -> Planner -> Implementer -> verification -> PR/CI workflow;
7. creates the resulting PR against the normal configured integration branch.

The policy `BaseSha` remains the development-base identity. The continuation SHA participates separately in implementation source identity and the first AutoDev-generated commit parent.

## Replacing the source of an existing run

`autodev resume --continue-from <ref>` is an explicit operator request to replace the current run's implementation source.

AutoDev preserves the previous durable source/checkpoint evidence under:

```text
.autodev-run/current/continuations/<continuation-id>/
```

It then adopts the new immutable commit, creates a fresh workspace baseline and AutoDev shipment branch, clears prior PR/CI/source verification proof, and invalidates Reader and dependent stages. Reader is intentionally rerun because repository evidence itself can become stale when the implementation source changes.

The repository identity, issue identity, development strategy, integration/release branches, and configured PR base remain unchanged.

If a human-directed revision is currently active, finish/resume that revision first. A continuation can then be adopted and a later `autodev revise` can change accepted requirements/plans if needed.

## Interrupted runs

Continuation identity is durable. Once adoption has completed, ordinary:

```text
autodev resume
```

uses the persisted continuation SHA and refuses unexpected source/HEAD drift. Repeating `--continue-from` is not required after an interruption.

## Status

For runs with an adopted source, AutoDev status reports the concepts separately:

```text
Continuation source: feature/existing-work
Resolved continuation SHA: abc123...
Development base: develop
PR target: develop
```

That separation is the core contract of this feature: existing work may be adopted without disguising it as repository branch policy.

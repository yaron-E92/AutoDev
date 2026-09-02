# Shared version policy

AutoDev provides one GitHub Actions version-policy component for repositories that use the ecosystem convention:

```text
+semver: major
+semver: minor
+semver: patch
+semver: none
```

The shared component is intentionally independent of the caller's implementation language. A .NET, TypeScript, Python, mixed-language, or otherwise unrelated repository does not install a product dependency merely to resolve versions.

## Lifecycle boundary

Version advancement and release publication are separate lifecycle decisions:

```text
pull request
  -> validate exactly one +semver intent (read-only)
  -> merge
  -> required main CI succeeds
  -> resolve highest eligible intent since latest canonical tag
  -> create annotated vMAJOR.MINOR.PATCH tag
  -> stop

manual release later
  -> select existing trusted tag
  -> build/sign/verify/package/publish for that exact tag
```

Creating a version tag does not publish a GitHub Release, package, signing job, deployment, or store artifact.


## AutoDev-created pull requests

The shared version-policy workflow remains strict: a PR must contain exactly one explicit directive. AutoDev's issue-to-PR workflow satisfies that contract by construction rather than weakening the validator.

For a newly prepared AutoDev run, the resolved intent is persisted in durable state and used for all later PR/CI repair cycles. Precedence is:

1. exactly one directive in the source GitHub issue;
2. explicit `--semver major|minor|patch|none` on `autodev issue-to-pr`;
3. repository `.autodev/repo.json` `default_semver_intent`;
4. built-in `patch`.

The generated PR body removes any directive line from the embedded issue copy and appends one canonical resolved directive, so the final body contains exactly one. Duplicate/conflicting issue directives and contradictory issue/CLI intent fail closed before role work begins.

Manual/non-AutoDev PRs are not silently defaulted by this behavior; they continue to own their explicit version intent themselves.

## Components

### JavaScript Action

`.github/actions/version-policy`

The Action is dependency-free JavaScript executed by GitHub's Action runtime. It owns deterministic version semantics and git/GitHub resolution:

- exact `+semver:` parsing;
- canonical `vMAJOR.MINOR.PATCH` history;
- highest explicit bump across associated merged pull requests;
- no implicit patch for legacy/unannotated direct commits;
- stale-main detection;
- annotated/idempotent tag allocation;
- conflicting-tag refusal.

It exposes:

- `base_tag`
- `base_version`
- `bump`
- `version`
- `tag`
- `source_sha`
- `tag_required`
- `superseded`
- `tag_status`
- `intents`

### Reusable PR workflow

`.github/workflows/version-intent.yml`

This workflow is read-only. It checks out the caller repository with full history, resolves the pull request's **current** body through the GitHub API when a PR number is available, executes the Action in `check-pr` mode, and exposes the candidate version outputs. The caller-supplied body remains only a compatibility fallback for contexts without a PR number.

Because the body is re-fetched at execution time, GitHub's ordinary **Re-run failed jobs** action evaluates the current PR text rather than the stale body captured by the original event.

### Reusable trusted-main workflow

`.github/workflows/version-tag.yml`

This workflow owns the narrow write boundary. It:

1. checks out the exact caller SHA;
2. serializes tag allocation per repository/branch;
3. resolves associated merged PR intents;
4. proves the candidate is still current `origin/<branch>`;
5. creates an annotated tag only when a bump is required;
6. stops without releasing anything.

The caller must grant sufficient `GITHUB_TOKEN` permissions for the called workflow. Reusable-workflow permissions can be maintained or reduced through a call chain, but not elevated above the caller's grant.

## Caller wiring

Consumers should pin the reusable workflow to an immutable AutoDev commit SHA.

Pull-request validation should run again when the PR body is edited:

```yaml
on:
  pull_request:
    branches: [main]
    types: [opened, synchronize, reopened, edited]

jobs:
  version-intent:
    if: github.event_name == 'pull_request'
    uses: yaron-E92/AutoDev/.github/workflows/version-intent.yml@<AUTODEV_COMMIT_SHA>
    with:
      pr_body: ${{ github.event.pull_request.body }}
      pr_number: ${{ github.event.pull_request.number }}
      head: ${{ github.sha }}
    permissions:
      contents: read
      pull-requests: read
```

Trusted-main allocation should depend on every required repository-specific CI job:

```yaml
jobs:
  version-tag:
    if: >-
      github.event_name == 'push' &&
      github.ref == 'refs/heads/main' &&
      always() &&
      needs.build.result == 'success' &&
      needs.test.result == 'success'
    needs:
      - build
      - test
    uses: yaron-E92/AutoDev/.github/workflows/version-tag.yml@<AUTODEV_COMMIT_SHA>
    with:
      head: ${{ github.sha }}
      branch: main
    permissions:
      contents: write
      pull-requests: read
```

The product repository remains responsible for deciding which CI jobs are mandatory before version advancement.

## Recovering a failed PR intent check

The exact-one-directive rule remains strict. Recovery does not require a dummy source commit:

1. Edit the pull request body so it contains exactly one valid `+semver: major|minor|patch|none` line. A caller subscribed to the `edited` PR event starts a fresh version-intent validation automatically. In AutoDev's own CI, body-edit runs use a separate concurrency lane and skip source/build/package jobs, so correcting PR metadata cannot cancel or replay an in-progress source CI run.
2. Alternatively, after correcting the body, use GitHub's normal **Re-run failed jobs** action on the previous workflow run. The reusable workflow fetches the current PR body before validating it, so the stale triggering payload is not authoritative.

Duplicate/conflicting directives still fail. This recovery behavior does not silently add a default to manual/non-AutoDev PRs.

## Product-specific metadata

The shared resolver owns SemVer identity, not every platform's packaging rules.

Examples of thin caller-side adapters:

- .NET assembly/file/informational versions derived from the shared SemVer;
- NuGet package version derived from the shared SemVer;
- npm/web build metadata derived from the shared SemVer;
- Android numeric `versionCode` derived monotonically from the tagged version;
- MSIX/platform-specific version formatting derived from the same source version.

These adapters must not independently choose a bump or create/move tags.

## Existing GitVersion/custom resolvers

When migrating a repository:

1. make the shared Action authoritative for intent and tag allocation;
2. remove any implicit-patch default;
3. remove tag writes from repository-specific GitVersion/custom jobs;
4. keep GitVersion temporarily only when it still provides useful derived metadata;
5. migrate those derived values to thin adapters over the shared output when practical.

Merge-message preservation is no longer the authority for release intent because the PR body is queried through GitHub's commit-to-pull-request association API.

## Updating consumers

Cross-repository consumers should use a full AutoDev commit SHA, not `main` or a floating action tag. Upgrading the shared policy is therefore intentional:

1. merge and validate the new AutoDev implementation;
2. record its exact immutable SHA;
3. update consumer workflow references in small repository-specific PRs;
4. allow each consumer CI to validate the upgrade before merge.

This prevents a central workflow edit from silently changing version behavior across every repository at once.

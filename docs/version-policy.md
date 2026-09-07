# Shared version policy

AutoDev provides one language-neutral GitHub Actions version-policy engine for repositories that use the ecosystem convention:

```text
+semver: major
+semver: minor
+semver: patch
+semver: none
```

The same engine supports both repository development strategies defined by `.autodev/repo.json`: backward-compatible `trunk` and opt-in `git-flow`.

## Development strategies

### Trunk

Repositories with no `development` configuration keep the historical behavior exactly:

```text
autodev/issue-N -> main
                    |
                    v
              required CI
                    |
                    v
        resolve highest +semver
                    |
                    v
          annotated version tag
```

The repository default/trusted branch is both the integration and release branch.

### Git Flow

A Git-Flow repository declares separate integration and release branches:

```json
{
  "version": 1,
  "development": {
    "strategy": "git-flow",
    "integration_branch": "develop",
    "release_branch": "main"
  }
}
```

Ordinary AutoDev work targets `develop` and still carries exactly one explicit `+semver` directive, but merging that PR does **not** create a public version tag. Release intent accumulates in the integration history.

```text
autodev/issue-101 --patch--> develop
                   #102 --minor--> develop
                   #103 --patch--> develop
                                |
                                v
                        develop -> main
                                |
                                v
                         release-branch CI
                                |
                                v
                 highest promoted intent = minor
                                |
                                v
                         one version tag
```

The `develop -> main` promotion PR does not need to restate a bump when one or more contributing integration PRs exist since the latest canonical release tag. The shared resolver inspects the promoted commits and their GitHub-associated PRs, collects the explicit intents from PRs that actually merged into the configured integration branch, and reports the contributing PRs in release diagnostics.

When contributing integration PRs exist, their highest intent is authoritative. A promotion-level directive is optional and non-authoritative; in particular, `+semver: none` on the promotion cannot suppress a derived `patch`, `minor`, or `major` bump. If no integration PR contributes an intent in the release window, the promotion PR must contain exactly one explicit `+semver` directive as the deliberate fallback release decision.

A promotion containing only integration PRs with `+semver: none` creates no tag and does not require a promotion-level directive.

## Lifecycle boundary

Version advancement and release publication remain separate lifecycle decisions in both strategies. Creating a version tag does not publish a GitHub Release, package, signing job, deployment, or store artifact.

For trunk:

```text
pull request -> main
  -> validate exactly one +semver intent
  -> merge
  -> required main CI succeeds
  -> resolve eligible intent since latest canonical tag
  -> create annotated vMAJOR.MINOR.PATCH tag when required
  -> stop
```

For Git Flow:

```text
ordinary pull requests -> develop
  -> validate one +semver each
  -> merge without public version tag

intentional promotion develop -> main
  -> derive highest promoted integration intent
  -> require explicit promotion intent only when no integration PR contributes
  -> release CI succeeds
  -> create at most one annotated vMAJOR.MINOR.PATCH tag
  -> stop
```

## AutoDev-created pull requests

The validator remains strict for ordinary trunk/integration PRs and direct release-branch hotfixes: each must contain exactly one explicit directive. Git-Flow integration-to-release promotions are the deliberate exception because their release intent is normally derived from the contributing integration PRs rather than restated manually.

For a newly prepared AutoDev run, the resolved intent is persisted in durable state and used for later PR/CI repair cycles. Precedence is:

1. exactly one directive in the source GitHub issue;
2. explicit `--semver major|minor|patch|none` on `autodev issue-to-pr`;
3. repository `.autodev/repo.json` `default_semver_intent`;
4. built-in `patch`.

The generated ordinary PR body removes directive lines from the embedded issue copy and appends one canonical resolved directive. Duplicate/conflicting issue directives and contradictory issue/CLI intent fail closed before role work begins.

Manual/non-AutoDev ordinary PRs and direct hotfix PRs continue to own their explicit version intent themselves. A manual Git-Flow promotion uses the same derived-or-fallback promotion contract described above.

## Direct release-branch hotfixes in Git Flow

Urgent work may deliberately target the configured release branch directly. Such a PR is not an ordinary integration PR and must contain exactly one explicit `+semver` directive.

Only history actually reachable from the release head is considered. Unpromoted `develop` work therefore cannot leak into a hotfix bump.

After a hotfix is released, `develop` must contain the current released history before a later promotion. The version engine checks this invariant using Git ancestry. A stale integration branch is refused with an actionable synchronization error rather than being tagged optimistically.

Example:

```text
main:     v1.5.0 -- hotfix -- v1.5.1
                          \
develop:  previous work --- sync main --- more work
```

Promoting `develop` before that synchronization is rejected.

## Shared Action

`.github/actions/version-policy`

The dependency-free JavaScript Action owns deterministic version semantics:

- exact `+semver:` parsing for PR roles that require explicit intent;
- Git-Flow promotion-role classification and derived/fallback intent selection;
- canonical `vMAJOR.MINOR.PATCH` history;
- repository `trunk` / `git-flow` policy parsing;
- highest explicit bump resolution;
- promoted integration-PR intent aggregation;
- direct release/hotfix isolation;
- stale release-head detection;
- release ancestry validation after hotfixes;
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
- `contributors`

`contributors` identifies the PRs/commits whose explicit intents were used, so a Git-Flow release decision is inspectable rather than magical.

## Reusable PR workflow

`.github/workflows/version-intent.yml`

This workflow is read-only. When a PR number is available it resolves the current authoritative PR body, base branch, and head branch through the GitHub API, checks out the caller repository with full history, executes the Action in `check-pr` mode, and exposes the candidate version and contributing-intent outputs.

The internal policy Action is referenced with `$/.github/actions/version-policy`, not `./.github/actions/version-policy`. In a cross-repository reusable workflow, `$` resolves against the repository and exact commit containing the running reusable workflow, while `./` resolves against the checked-out workspace. Because `version-intent.yml` deliberately checks out the caller repository at the workspace root, using `$` keeps AutoDev's implementation bound to the same immutable AutoDev commit selected by the consumer while the Action still inspects the caller repository.

A metadata-only rerun therefore validates the current PR metadata rather than relying on stale event text. This branch-role context is what lets the shared policy distinguish an ordinary integration PR, a Git-Flow `develop -> main` promotion, and a direct release-branch hotfix.

## Reusable trusted-tag workflow

`.github/workflows/version-tag.yml`

This workflow owns the narrow write boundary. It:

1. checks out the exact successful caller SHA;
2. serializes tag allocation per repository/branch;
3. loads repository development policy;
4. resolves trunk or Git-Flow release intent through the same Action;
5. proves the candidate is still current `origin/<release branch>`;
6. creates an annotated tag only when a bump is required;
7. reports contributing intents;
8. stops without releasing anything.

The caller must grant sufficient `GITHUB_TOKEN` permissions. Reusable-workflow permissions can be maintained or reduced through a call chain, but not elevated above the caller's grant.

## Caller wiring

GitHub branch filters are static, so repository policy cannot change an already-parsed `on.pull_request.branches` block dynamically. A Git-Flow-capable caller should listen to the stable branch set it supports (typically `develop` and `main`) and let the policy engine determine the branch role.

For pull-request metadata validation, for example:

```yaml
on:
  pull_request:
    branches: [main, develop]
    types: [edited]
```

Source/build/test CI should likewise run for ordinary integration PRs. AutoDev itself uses a thin `ci-develop.yml` caller so PRs into `develop` execute the same reusable CI implementation as trunk/main validation instead of maintaining a second test suite.

Repositories using required status checks should require a stable aggregate source-validation gate and the version-intent check on the branch where ordinary work lands.

Trusted tag allocation must run only for the configured release/trunk branch after all required release-branch CI succeeds. Do not invoke public version tagging merely because `develop` advanced.

## Recovering a failed PR intent check

For ordinary trunk/integration PRs and direct release-branch hotfixes, the exact-one-directive rule remains strict. Recovery does not require a dummy source commit:

1. edit the pull request body so it contains exactly one valid `+semver: major|minor|patch|none` line; or
2. after correcting the body, use GitHub's normal **Re-run failed jobs** action.

For a Git-Flow integration-to-release promotion, first inspect the version-intent summary. If contributing integration PRs are present, no promotion-level directive is required and the highest contributing intent is authoritative. If no integration PR contributes, add exactly one explicit promotion-level directive as the fallback decision.

The reusable workflow fetches the current PR metadata before validation. Duplicate/conflicting directives still fail where a directive is used.

## Migration from trunk to Git Flow

Use an explicit cutover rather than silently changing branch meaning underneath active work:

```text
1. update AutoDev to a release containing Git-Flow support
2. finish or explicitly recover any incompatible active AutoDev run
3. create develop from the current released main head
4. add development.strategy = git-flow with develop/main branch roles
5. ensure CI and version-intent callers run for develop PRs
6. update branch protection / required checks for develop and main
7. point Dependabot and ordinary automated update PRs at develop where appropriate
8. verify the next AutoDev issue PR is prepared from and targets develop
9. accumulate validated work on develop
10. promote develop -> main only when intentionally cutting a release
```

After a direct main hotfix or normal release promotion, synchronize the current released `main` ancestry back into `develop` before the next promotion if it is not already present.

## Product-specific metadata

The shared resolver owns SemVer identity, not every platform's packaging rules. Thin caller-side adapters may derive .NET assembly versions, NuGet/npm package versions, Android version codes, MSIX versions, and similar metadata from the shared SemVer, but they must not independently choose a bump or create/move tags.

## Updating consumers

Cross-repository consumers should use a full AutoDev commit SHA, not `main`, `develop`, or a floating action tag. Upgrading shared policy is intentional:

1. merge and validate the new AutoDev implementation;
2. record its exact immutable SHA;
3. update consumer workflow references in small repository-specific PRs;
4. let each consumer CI validate the upgrade before merge.

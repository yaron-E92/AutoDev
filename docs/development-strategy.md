# Repository development strategy

AutoDev supports two repository development strategies through `.autodev/repo.json`.

## Trunk (default)

Existing repositories require no migration. With no `development` object, AutoDev preserves trunk behavior and uses the repository's normal trusted branch semantics.

An explicit form is:

```json
{
  "version": 1,
  "development": {
    "strategy": "trunk",
    "integration_branch": "main",
    "release_branch": "main"
  }
}
```

The integration and release branches must resolve to the same branch.

## Git Flow

For repositories that want many validated implementation PRs to accumulate before one release promotion:

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

Ordinary AutoDev work is prepared from `develop`, issue PRs target `develop`, and scheduler workers use `develop` as their normal work branch. `main` remains the released/trusted branch.

Configure the policy through repository setup when desired:

```text
autodev repo install --development-strategy git-flow
```

Custom branch names can be supplied explicitly:

```text
autodev repo install \
  --development-strategy git-flow \
  --integration-branch integration \
  --release-branch production
```

AutoDev validates branch/ref names before using them. `repo doctor` reports the effective strategy, normal PR target, and whether the configured integration/release branches exist.

## Durable runs and strategy changes

The effective strategy and branch roles are persisted when an issue is prepared. AutoDev refuses to silently resume a run if repository policy later changes its branch meaning.

If a run was prepared under the old strategy, inspect any existing implementation branch/PR and either finish/recover it under the original policy or intentionally restart it after resolving that state.

An explicit legacy `BASE_BRANCH` override remains supported and is persisted as an override rather than being confused with policy drift.

## CI and versioning

Every ordinary feature/fix PR still carries exactly one canonical `+semver` intent.

In trunk mode, trusted-branch CI may advance the canonical version tag as before.

In Git-Flow mode:

- PRs merged into `develop` validate and accumulate their intents but do not create public version tags;
- a later `develop -> main` promotion derives one bump from the highest promoted eligible intent;
- the promotion creates at most one canonical tag after required `main` CI;
- a direct `main` hotfix requires its own explicit intent and does not include unrelated unpromoted `develop` work;
- after a released hotfix, `develop` must contain the current released ancestry before a later promotion.

See [`version-policy.md`](version-policy.md) for the release-intent algorithm and examples.

## Branch protection and automated PRs

For Git-Flow repositories, configure repository policy consistently:

- require ordinary source/version checks on `develop`;
- keep release/trusted checks on `main`;
- direct pushes should not bypass those gates;
- Dependabot and similar ordinary update PRs should normally target `develop`;
- release promotion remains an explicit `develop -> main` decision.

AutoDev can diagnose branch existence and workflow intent, but repository rulesets/branch protection may require separate GitHub administration permissions.

## Adoption sequence

```text
1. finish or recover incompatible active AutoDev work
2. create develop from the current released main
3. set development.strategy = git-flow
4. run autodev repo install / repo doctor
5. update branch rules and automated PR targets
6. verify the next AutoDev issue PR targets develop
7. accumulate validated work on develop
8. promote develop -> main only when intentionally releasing
```

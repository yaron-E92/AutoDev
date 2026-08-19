# Shared CI workflow profiles

AutoDev hosts reusable GitHub Actions workflows for concerns that should be consistent across repositories. The caller repository keeps only its triggers, repository-specific commands, secrets, product/platform exceptions, and the dependency graph between shared and local jobs.

The profiles are designed to reduce copied YAML without forcing one repository's build graph onto every other project.

## Principles

1. **Pin shared workflows immutably.** External repositories must call AutoDev reusable workflows at an exact commit SHA, never `main`, a branch, or a mutable version tag.
2. **Keep permissions least-privilege.** Shared validation profiles default to `contents: read`. A caller grants write scopes only to the specific job that needs them, such as trusted version-tag allocation.
3. **Keep build commands explicit.** AutoDev standardizes setup, caching, matrices, result handling, artifacts, summaries, and policy. The caller supplies solution/project paths and product-specific commands.
4. **Separate versioning from release.** Shared `+semver` intent and trusted post-main tagging may run automatically. Release/build-sign-publish workflows remain explicitly invoked unless a repository makes a separate reviewed policy decision.
5. **Do not add project dependencies merely for CI.** The shared Actions use GitHub-hosted runtimes. A .NET repository does not become a Node project because it consumes a JavaScript Action.

## Profiles

### `profile-baseline.yml`

Validates workflow-level policy independently of application language:

- every workflow declares explicit permissions;
- `permissions: write-all` is rejected;
- external `uses:` references are pinned to full commit SHAs;
- local (`./`) and AutoDev self-repository (`$/`) references are allowed;
- actionlint runs by default;
- caller concurrency can be required when the repository is ready to standardize it.

Example:

```yaml
jobs:
  baseline:
    uses: yaron-E92/AutoDev/.github/workflows/profile-baseline.yml@<AUTODEV_COMMIT_SHA>
    with:
      ref: ${{ github.sha }}
      require_concurrency: true
    permissions:
      contents: read
```

### `profile-python.yml`

Standardizes Python runner/version matrices and command ordering.

Inputs include:

- JSON `runners` and `python_versions` matrices;
- optional setup-python dependency caching;
- install, compile/import-smoke, lint, type-check, test, and package commands;
- optional result/build artifact upload with `if-no-files-found: error`.

The caller owns the commands. AutoDev owns the matrix/setup/artifact convention.

### `profile-dotnet.yml`

Standardizes .NET setup and validation:

- SDK selection from `global.json`, or an explicit version when `global_json_file` is empty;
- optional setup-dotnet NuGet caching;
- optional caller setup command for feeds/workloads;
- restore → build → test → optional lint/analyzer ordering;
- optional required test-result artifact, which fails when the configured result path produces no files;
- an optional package-feed token exposed only as `NUGET_AUTH_TOKEN`.

`cache: true` is opt-in because setup-dotnet package caching expects lockfiles. Repositories without lockfiles should leave it disabled until their dependency policy supports it.

### `profile-maui.yml`

Composes `profile-dotnet.yml` rather than copying .NET setup into each platform leg.

It provides independently controllable:

- shared/headless validation;
- Android product-shape validation;
- Windows product-shape validation.

Android and Windows are enabled by default. Their restore/build/test commands deliberately fail with a configuration message until the caller supplies project-specific commands. A repository may explicitly disable a platform leg when it is genuinely not applicable; path filtering or convenience logic must not silently turn required product validation into a no-op.

MAUI workload/platform setup remains an explicit command input because project/workload needs differ. As migrations expose repeated safe caching patterns, those patterns should move into this shared profile rather than being copied between consumers.

### `profile-node-next.yml`

Standardizes Node and Next.js validation:

- pinned setup-node runtime;
- optional lockfile-aware package-manager caching;
- optional package-manager setup (for example Corepack);
- deterministic install, lint, type-check, test, and production-build ordering;
- optional build artifact upload.

The profile does not assume Vercel or any other deployment provider. Deployment remains a caller/release concern.

## Version workflows

The version-policy workflows remain separate reusable components:

- `version-intent.yml` validates exactly one `+semver: major|minor|patch|none` on pull requests and computes a read-only candidate;
- `version-tag.yml` allocates the trusted annotated `vMAJOR.MINOR.PATCH` tag after the caller's required main CI succeeds.

A CI profile does not publish a release merely because it creates or validates a version.

## Extension points

Repository-specific behavior belongs in one of three places:

1. **profile command inputs** when the behavior is part of normal validation;
2. **local caller jobs** when the behavior is genuinely unique (emulator orchestration, specialized data services, generated fixtures, etc.);
3. **manual release/signing workflows** for protected publication concerns.

If the same local extension appears in multiple consumers, promote it into a shared profile rather than copying it again.

## Secrets

Reusable validation profiles do not inherit arbitrary secrets automatically. Pass only the explicitly required secret to the shared workflow. Product signing credentials, deployment credentials, and publication identities should not be passed into ordinary CI profiles.

Never build a reusable-profile command string from untrusted pull-request text. Command inputs are static workflow configuration; PR content belongs only in data inputs designed for it, such as the version-intent workflow's `pr_body`.

## Shared API evolution

Consumers pin exact AutoDev commits, so changes do not silently alter existing repositories.

For the shared workflow API:

- additive optional inputs and bug fixes may evolve the existing profile filename;
- a breaking input/semantic change should introduce a new profile filename/version (for example `profile-dotnet-v2.yml`) and a documented migration path;
- consumers migrate intentionally and pin the new reviewed AutoDev commit;
- old profile versions should remain available long enough to migrate the active S fleet.

## Migration procedure

For each repository selected for standardization:

1. inventory current jobs and required checks;
2. classify jobs into shared profile concerns versus repository-specific extensions;
3. preserve or improve existing validation strength before deleting copied steps;
4. migrate to thin immutable reusable-workflow calls;
5. run PR CI and compare behavior/check names/artifacts;
6. update required checks/rulesets when authorized;
7. merge only after the new shared path is green;
8. observe the first trusted main run, including version-tag behavior when enabled;
9. leave release publication manual unless that repository has an explicit separate decision otherwise.

The authoritative S/M/K/B rollout scope is maintained in AutoDev issue #167. M repositories are audit-only until explicitly promoted; K repositories are excluded; Goldilocks is blocked until repository access is restored.

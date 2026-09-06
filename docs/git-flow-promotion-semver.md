# Git-Flow promotion SemVer

For repositories configured with `development.strategy = git-flow`, SemVer authority depends on the pull-request role.

- Ordinary feature/fix PRs into the integration branch must contain exactly one `+semver: major|minor|patch|none` directive.
- Direct hotfix PRs into the release branch must contain exactly one explicit directive.
- A promotion from the configured integration branch to the configured release branch normally does **not** need its own `+semver` directive. AutoDev derives the promotion bump from the highest explicit intent on contributing integration PRs since the latest reachable canonical release tag.

A promotion-level directive is non-authoritative when contributing integration PRs exist. In particular, `+semver: none` on the promotion cannot suppress a derived `patch`, `minor`, or `major` bump.

If no integration PR contributes an intent in the release window, the promotion PR must contain exactly one explicit `+semver` directive. That directive is then the deliberate fallback release intent.

This keeps the normal Git-Flow path declarative while preserving an explicit version decision for unusual promotions made only from direct integration-branch commits.

# Deferred Windows verification

AutoDev does not treat Linux verification as proof that Windows-only checks passed. A successful local check may emit `DEFERRED:` obligations; Windows/WinUI/`-windows` obligations are persisted and, when required by repository policy, are verified by GitHub Actions on `windows-latest`.

## Stable target workflow

Each target repository installs one AutoDev-owned workflow at:

```text
.github/workflows/autodev-windows-verification.yml
```

The workflow does **not** embed the AutoDev commit that installed it. Instead, every AutoDev run dispatches four inputs:

```text
expected_sha     exact target-repository commit to verify
source_identity  AutoDev shipped-source identity
autodev_ref      exact 40-character AutoDev commit running the workflow
commands_json    configured Windows verification commands
```

The job checks out the target repository at `expected_sha`, checks out `yaron-E92/AutoDev` at `autodev_ref`, and executes `windows/scripts/windows-verification-worker.ps1` from that ephemeral AutoDev checkout. No SSH host, permanent Windows machine, or fixed `C:\AutoDev` path is required.

Because `autodev_ref` is supplied at dispatch time, updating AutoDev does **not** normally require committing a new workflow revision in every target repository. Reinstall and commit the target workflow when the workflow protocol/template or the repository's `setup` configuration changes.

## Installation

Install or refresh target-repository AutoDev assets with the public repository setup command:

```text
autodev repo install
```

This is the canonical user-facing path. The internal Python installer modules are implementation details and are not required for normal repository setup.

If `.github/workflows/autodev-windows-verification.yml` is new or changed, commit and merge it to the target repository's default branch. GitHub requires a `workflow_dispatch` workflow to exist on the default branch before it can be dispatched. AutoDev preflight checks that Actions is enabled and that the configured workflow is visible there.

## Repository configuration

Repositories that need Windows verification configure `.autodev/windows-verification.json`, for example:

```json
{
  "version": 1,
  "enabled": true,
  "when": "deferred-windows",
  "timeout_seconds": 3600,
  "workflow": "autodev-windows-verification.yml",
  "setup": {
    "name": "Configure repository package sources",
    "command": "& \"$env:GITHUB_WORKSPACE\\autodev-tooling\\windows\\scripts\\configure-nuget-source.ps1\" -SourceUrl 'https://nuget.pkg.github.com/PACKAGE_OWNER/index.json' -SourceName 'private-github' -Username 'PACKAGE_OWNER'",
    "secret_env": {
      "NUGET_TOKEN": "REPOSITORY_PACKAGE_TOKEN"
    }
  },
  "commands": [
    {
      "name": "windows-publish",
      "command": "dotnet publish MyApp.csproj -c Release -f net10.0-windows10.0.19041.0"
    },
    {
      "name": "windows-smoke",
      "command": "pwsh -NoProfile -File scripts/windows-smoke.ps1"
    }
  ]
}
```

`when` may be `deferred-windows` or `always`. `deferred-windows` dispatches only when local verification recorded an explicit Windows obligation. `always` runs the configured Windows commands for every shipped AutoDev patch.

### Repository setup

`setup` is optional. When configured, `autodev repo install` renders one repository-specific PowerShell setup step after both exact checkouts and before the AutoDev verification worker. The command runs from the checked-out target repository and can invoke either repository tooling or tooling from the exact AutoDev revision under `$env:GITHUB_WORKSPACE\autodev-tooling`.

`secret_env` maps the environment-variable contract used by the command to the actual GitHub Actions secret name in that repository. Only secret names are rendered into the installed workflow; values remain in GitHub Actions and are exposed only to the generated setup step. This allows repositories to use different secret names while presenting the same variable, such as `NUGET_TOKEN`, to AutoDev tooling. Missing mapped secrets fail with an explicit setup error before any product verification command starts.

AutoDev includes `windows/scripts/configure-nuget-source.ps1` for private NuGet feeds. It accepts the source URL, source name, and username as non-secret parameters, requires an HTTPS source, and reads the credential only from `NUGET_TOKEN`. The example configuration invokes that helper from the already-pinned AutoDev checkout; no helper is copied into the target repository and normal CI remains independent of AutoDev.

After adding or changing `setup`, rerun `autodev repo install` and merge the regenerated caller workflow to the default branch. A reusable workflow is not used for this hook because GitHub reusable workflows replace an entire job; the setup must run inside the exact-SHA Windows verification job.

## Execution order

Windows verification runs after local and semantic verification have produced a verified commit, but it does not need to wait for PR creation:

```text
local verification
  -> semantic verification
  -> create/push exact AutoDev issue-branch commit
  -> dispatch target Windows workflow
  -> GitHub windows-latest verification
  -> create/update PR
  -> normal required PR CI
  -> ready proof
```

AutoDev records the workflow runs that existed before dispatch and accepts only a new `workflow_dispatch` run whose `headSha` equals `expected_sha`. The workflow separately checks `${{ github.sha }}` against that SHA, `actions/checkout` uses that exact target commit, and the PowerShell worker verifies the target checkout again with `git rev-parse HEAD` before running commands.

The exact `autodev_ref` used for the worker is persisted in `windows-verification-request.json`, `windows-verification-result.json`, and the successful Windows proof. A Windows code failure can enter the normal fixer loop. Failures before an AutoDev verification command starts remain infrastructure/setup failures rather than code-repair requests.

## Durable artifacts

AutoDev persists:

```text
.autodev-run/current/deferred-verification.json
.autodev-run/current/windows-verification-request.json
.autodev-run/current/windows-verification-result.json
.autodev-run/current/windows-repair.md   # only for code-repairable Windows failures
```

State and run-manifest metadata retain the Windows requirement, exact target source identity, exact AutoDev revision, GitHub Actions run identity, and repair/resume boundary. `ready` remains fail-closed if required Windows proof is missing, stale, or artifact-drifted.

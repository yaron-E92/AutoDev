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

The canonical installer is:

```text
python -m automation.opencode_install --target-repo <TARGET_REPOSITORY>
```

Use `python3` where appropriate. The older command:

```text
python -m automation.opencode_adapter install
```

is deprecated and remains only as a compatibility shim that delegates to the canonical installer.

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
    "command": "pwsh -NoProfile -File .github/scripts/configure-packages.ps1",
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

### Shared repository setup

`setup` is optional. When configured, the installer renders one repository-specific PowerShell setup step before the AutoDev verification worker. The command runs from the checked-out target repository, so a repository can keep package-feed, SDK, or tool setup in one versioned script and call that same script from its normal CI workflow.

`secret_env` maps the environment-variable contract used by that script to the actual GitHub Actions secret name in that repository. Only secret names are committed; values remain in GitHub Actions and are exposed only to the generated setup step. This allows repositories to use different secret names while presenting the same variable, such as `NUGET_TOKEN`, to a shared script. Missing mapped secrets fail with an explicit setup error before any product verification command starts.

For example, normal CI can call the same script with its own static secret binding:

```yaml
- name: Configure repository package sources
  shell: pwsh
  env:
    NUGET_TOKEN: ${{ secrets.REPOSITORY_PACKAGE_TOKEN }}
  run: pwsh -NoProfile -File .github/scripts/configure-packages.ps1
```

After adding or changing `setup`, rerun the AutoDev installer and merge the regenerated caller workflow to the default branch. A reusable workflow is not used for this hook because GitHub reusable workflows replace an entire job; the setup must run inside the exact-SHA Windows verification job. A shared script or composite action is the appropriate boundary for regular CI, while AutoDev's hook currently invokes the shared script.

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

# Deferred Windows verification

AutoDev does not treat a Linux verification success as proof that Windows-only checks passed. When a successful local check emits lines beginning with `DEFERRED:`, AutoDev persists those obligations in `.autodev-run/current/deferred-verification.json` and `state.json`. Messages that explicitly identify Windows/WinUI/`-windows` targets become Windows obligations.

Windows verification uses **GitHub-hosted `windows-latest` runners in the target repository**. AutoDev does not require SSH, a separately managed Windows host, or a permanently installed `C:\AutoDev` worker.

## Architecture

AutoDev owns the reusable implementation in:

```text
.github/workflows/autodev-windows-verification.yml
```

Each target repository gets a tiny caller workflow at install time:

```text
.github/workflows/autodev-windows-verification.yml
```

The caller is pinned to the AutoDev revision that installed it. GitHub executes the reusable workflow in the **caller repository context**. The GitHub-hosted runner and `GITHUB_TOKEN` therefore belong to the target repository, and the target commit can be checked out without cross-repository credentials.

The reusable workflow requests `windows-latest`, verifies that the workflow dispatch ref is the exact SHA AutoDev requested, checks out that exact target commit, then checks out the same pinned AutoDev revision into the ephemeral runner workspace to obtain `windows/scripts/windows-verification-worker.ps1`. The worker exists only for the GitHub Actions job; no fixed Windows-machine path is required.

## Installation requirement

`python -m automation.opencode_install` installs the target caller workflow together with the normal AutoDev/OpenCode assets. **The generated workflow must be committed and merged into the target repository default branch.** GitHub only accepts `workflow_dispatch` for workflows that exist on the default branch.

When a repository has an enabled `.autodev/windows-verification.json`, AutoDev preflight asks GitHub whether Actions is enabled and whether the configured caller workflow is visible on the target default branch. If it is missing, the run fails with an actionable instruction to rerun the installer and commit/merge the generated workflow. AutoDev does not wait until the end of an expensive run to discover that the Windows lane does not exist.

## Repository configuration

Create `.autodev/windows-verification.json` in a repository that needs Windows verification:

```json
{
  "version": 1,
  "enabled": true,
  "when": "deferred-windows",
  "timeout_seconds": 3600,
  "workflow": "autodev-windows-verification.yml",
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

`when` is either `deferred-windows` or `always`:

- `deferred-windows` dispatches the Windows workflow only after the local verification evidence contains an explicit Windows obligation.
- `always` requires the configured Windows commands for every shipped AutoDev patch in the repository.

The workflow is triggered with `workflow_dispatch` rather than running on every push. That lets AutoDev use its actual platform-deferred evidence as the condition and pass the exact commit SHA, source identity, and configured commands as inputs. It avoids consuming Windows minutes for unrelated patches.

## It runs before the PR

After local and semantic verification pass, AutoDev creates/pushes the verified commit to its AutoDev issue branch. If Windows verification is required, AutoDev immediately dispatches the target repository workflow against that branch **before opening the pull request**.

The sequence is:

```text
local verification
  -> semantic verification
  -> create/push exact AutoDev commit
  -> workflow_dispatch on target repo / AutoDev branch
  -> GitHub windows-latest verification
  -> open/update PR
  -> normal required PR CI
  -> ready proof
```

A Windows code failure therefore returns to the fixer without needing to create a PR first. After repair, AutoDev reruns local verification, semantic verification, pushes a new exact commit, and dispatches Windows verification again. Infrastructure failures remain infrastructure failures.

If a PR already exists (for example during a later repair), the dispatched SHA must still equal the current PR head before the proof can become final.

## GitHub Actions identity and access guarantees

AutoDev records the set of workflow runs that existed before dispatch, dispatches the caller workflow with `--ref <autodev-branch>`, then accepts only a newly created `workflow_dispatch` run whose `headSha` is exactly the pushed commit.

The reusable workflow independently rejects a stale dispatch when `${{ github.sha }}` differs from the requested SHA, and `actions/checkout` checks out the requested SHA explicitly. The PowerShell worker then runs `git rev-parse HEAD` and refuses to continue unless the checkout still matches.

The caller workflow runs in the target repository context, so the GitHub-hosted runner receives the target repository's normal `GITHUB_TOKEN`/checkout access. AutoDev does not need a token from the AutoDev repository to read another repository.

## Conditions and restrictions

The default design intentionally uses a narrow manual trigger:

- `workflow_dispatch` only;
- dispatch performed by AutoDev only when `WindowsVerificationRequired` is true;
- exact AutoDev branch and SHA passed as inputs;
- `contents: read` permissions for the Windows job;
- reusable workflow pinned by the installed caller to an AutoDev revision;
- no repository write permission required inside the Windows job;
- no secrets are forwarded to the reusable workflow.

Repositories may add their own static `push`/`pull_request` Windows workflows if desired, but AutoDev does not depend on those broad triggers for its proof.

## Durable evidence and resume

AutoDev persists:

- `.autodev-run/current/deferred-verification.json` — local platform obligations and safe config metadata;
- `.autodev-run/current/windows-verification-request.json` — target repo, workflow, exact branch/SHA/source identity and commands dispatched;
- `.autodev-run/current/windows-verification-result.json` — GitHub Actions run ID, URL, conclusion, exact SHA/source identity and bounded failure evidence;
- `.autodev-run/current/windows-repair.md` — only when the Windows job reached an AutoDev verification command and produced code-repairable evidence;
- `state.json` and `run-manifest.json` — requirement/proof/failure state used by status and resume.

A successful Windows result is accepted only for a completed successful GitHub Actions run associated with the exact pushed SHA. The proof is then tied to AutoDev's already-verified shipped source identity. `ready` refuses to complete while a required Windows proof is missing, stale, or artifact-drifted.

If the Actions run fails before an AutoDev Windows command starts, is cancelled/timed out, or contains known transient infrastructure evidence, AutoDev treats it as infrastructure rather than asking the fixer to change code.

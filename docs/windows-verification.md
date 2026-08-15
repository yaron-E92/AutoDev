# Deferred Windows verification

AutoDev does not treat a Linux verification success as proof that Windows-only checks passed. When a successful local check emits lines beginning with `DEFERRED:`, AutoDev persists those obligations in `.autodev-run/current/deferred-verification.json` and `state.json`. Messages that explicitly identify Windows/WinUI/`-windows` targets become Windows obligations.

The Windows lane runs only after AutoDev has created the shipped commit/PR and required CI has reached terminal success. This means the worker always receives the exact PR head SHA plus AutoDev's shipped source identity rather than an uncommitted worktree approximation.

## Repository configuration

Create `.autodev/windows-verification.json` in a repository that needs an automated Windows lane. The schema is version 1:

```json
{
  "version": 1,
  "enabled": true,
  "when": "deferred-windows",
  "timeout_seconds": 3600,
  "runner": [
    "ssh",
    "windows-builder",
    "pwsh",
    "-NoProfile",
    "-File",
    "C:\\AutoDev\\windows\\scripts\\windows-verification-worker.ps1"
  ],
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

`when` is either `deferred-windows` or `always`. `deferred-windows` is the non-disruptive default: repositories without a Windows obligation do not need a Windows runner. `always` declares that every shipped AutoDev patch in that repository requires the configured Windows commands.

`runner` is an argv array, not a shell string. It can invoke SSH, a local Windows PowerShell process, or another explicit transport that starts the worker. Do not put credentials in the config. The worker's Git installation/host environment should already have whatever repository access it needs. `repository_url` may optionally override the default `https://github.com/<owner>/<repo>.git`, for example to use an SSH remote.

The checked-in worker is `windows/scripts/windows-verification-worker.ps1`. It reads one JSON request on stdin, creates a clean temporary worktree, fetches and checks out the exact requested commit, executes the configured commands, and returns one JSON result on stdout. It refuses to claim success when it is not running on Windows or when the checked-out commit differs from the requested SHA.

## Durable evidence and resume

AutoDev persists:

- `.autodev-run/current/deferred-verification.json` — local platform obligations and safe config metadata;
- `.autodev-run/current/windows-verification-request.json` — the exact commit/source identity and command request sent to the worker;
- `.autodev-run/current/windows-verification-result.json` — bounded Windows command evidence;
- `.autodev-run/current/windows-repair.md` — only when a Windows command fails with code-repairable evidence;
- `state.json` and `run-manifest.json` — requirement/proof/failure state used by status and resume.

A successful Windows result is accepted only when it reports `platform=windows`, the exact PR head SHA, and the same shipped source identity. `ready` refuses to complete while a required Windows proof is missing, stale, or artifact-drifted.

If a Windows command fails with ordinary build/test/publish evidence, the run enters the normal fixer path as a `windows` repair. The fix invalidates downstream verification, so AutoDev reruns deterministic checks, semantic verification, shipment/CI, and the Windows lane on the new PR head. Network/rate-limit/service failures are classified as infrastructure instead of code repair.

If a run is blocked because a Windows obligation exists but no runner is configured, add the repository config and resume. Existing successful CI proof remains durable, but AutoDev will not mark the PR ready until the Windows lane has produced current terminal-success evidence.

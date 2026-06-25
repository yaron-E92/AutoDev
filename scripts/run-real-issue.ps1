[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

function Show-Usage {
    @"
Run the real GitHub issue automation flow.

Examples:
  scripts\run-real-issue.ps1 --repo . --github-repo owner/AutoDev --issue 18 --mode plan-only --out .codex-run\real-issue-18
  scripts\run-real-issue.ps1 --repo . --github-repo owner/AutoDev --issue 18 --mode implement --out .codex-run\real-issue-18 --allow-dirty
  scripts\run-real-issue.ps1 --repo . --github-repo owner/AutoDev --issue 18 --mode pr --out .codex-run\real-issue-18
"@
}

foreach ($required in @("python", "gh")) {
    if (-not (Get-Command $required -ErrorAction SilentlyContinue)) {
        Write-Error "Missing required executable: $required"
        exit 127
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")

if ($Arguments.Count -gt 0 -and ($Arguments[0] -eq "--help" -or $Arguments[0] -eq "-h")) {
    Show-Usage
}

Set-Location $RepoRoot
& python (Join-Path $RepoRoot "automation\run_real_issue.py") @Arguments
exit $LASTEXITCODE

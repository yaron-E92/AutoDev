[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

function Show-Usage {
    @"
Run the AutoDev real GitHub issue automation flow.

Examples:
  scripts\run-real-issue.ps1 --repo . --github-repo owner/AutoDev --issue 18 --mode plan-only --out .autodev-runs\issue-18 --reader qwen35-9b-32k --coder devstral-small2-12k
  scripts\run-real-issue.ps1 --repo . --github-repo owner/AutoDev --next --manage-labels --mode implement --out .autodev-runs\next --provider-config autodev-providers.json
  scripts\run-real-issue.ps1 --repo . --github-repo owner/AutoDev --issue 18 --mode pr --out .autodev-runs\issue-18 --reader-provider chat-completions --reader-base-url http://localhost:1234/v1 --reader-model qwen35-9b-32k --coder-command "my-coder"
"@
}

foreach ($required in @("python", "gh")) {
    if (-not (Get-Command $required -ErrorAction SilentlyContinue)) {
        Write-Error "Missing required executable: $required"
        exit 127
    }
}

$ScriptPath = $MyInvocation.MyCommand.Path
$ScriptItem = Get-Item -LiteralPath $ScriptPath
while ($null -ne $ScriptItem.Target) {
    $TargetPath = $ScriptItem.Target
    if (-not [System.IO.Path]::IsPathRooted($TargetPath)) {
        $TargetPath = Join-Path $ScriptItem.DirectoryName $TargetPath
    }
    $ScriptItem = Get-Item -LiteralPath $TargetPath
}
$ScriptsDir = $ScriptItem.DirectoryName
$RepoRoot = (Resolve-Path (Join-Path $ScriptsDir "..")).Path

if ($Arguments.Count -gt 0 -and ($Arguments[0] -eq "--help" -or $Arguments[0] -eq "-h")) {
    Show-Usage
}

Set-Location $RepoRoot
& python (Join-Path $RepoRoot "automation\run_real_issue.py") @Arguments
exit $LASTEXITCODE

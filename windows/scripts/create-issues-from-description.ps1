[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

function Show-Usage {
    @"
Create structured GitHub issues from rough descriptions.

Examples:
  windows\scripts\create-issues-from-description.ps1 --description "Add dry-run mode to AutoDev" --repo owner/AutoDev --model devstral-small2-12k
  windows\scripts\create-issues-from-description.ps1 --description-file ideas.md --repo-map repo-map.json --dry-run
  windows\scripts\create-issues-from-description.ps1 --description "Fix AutoDev docs" --repo owner/AutoDev --model devstral-small2-12k --create --yes
"@
}

foreach ($required in @("python", "gh")) {
    if (-not (Get-Command $required -ErrorAction SilentlyContinue)) {
        Write-Error "Missing required executable: $required"
        exit 127
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

if ($Arguments.Count -gt 0 -and ($Arguments[0] -eq "--help" -or $Arguments[0] -eq "-h")) {
    Show-Usage
}

Set-Location $RepoRoot
& python (Join-Path $RepoRoot "automation\create_issues_from_description.py") @Arguments
exit $LASTEXITCODE

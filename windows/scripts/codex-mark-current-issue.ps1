param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ReadyForReview", "Blocked")]
    [string]$Status,

    [string]$Message = "",
    [string]$WorkingDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. "$env:USERPROFILE\codex-tools\codex-common.ps1"

function Set-OptionalWorkingDirectory {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }

    if (-not (Test-Path $Path)) {
        throw "Working directory does not exist: $Path"
    }

    Set-Location $Path
    Write-Host "Using working directory: $Path"
}

function Read-State {
    $path = ".codex-run/current/state.json"

    if (-not (Test-Path $path)) {
        throw "Missing state file: $path"
    }

    return Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-State {
    param(
        [Parameter(Mandatory = $true)]
        $State
    )

    $State |
        ConvertTo-Json -Depth 20 |
        Set-Content -Path ".codex-run/current/state.json" -Encoding UTF8
}

function Initialize-AuthFromState {
    param($State)

    Initialize-GitHubToken `
        -GitHubTokenSecretName ([string]$State.Auth.GitHubTokenSecretName) `
        -KeePassCliPath ([string]$State.Auth.KeePassCliPath) `
        -KeePassDatabasePath ([string]$State.Auth.KeePassDatabasePath) `
        -KeePassEntryPath ([string]$State.Auth.KeePassEntryPath) `
        -KeePassKeyFilePath ([string]$State.Auth.KeePassKeyFilePath) `
        -KeePassNoPassword:([bool]$State.Auth.KeePassNoPassword) `
        -GhConfigDir ([string]$State.Auth.GhConfigDir)
}

Set-OptionalWorkingDirectory -Path $WorkingDirectory

Require-Command gh

$state = Read-State
Initialize-AuthFromState -State $state

$issueNumber = [int]$state.IssueNumber
$repoFullName = [string]$state.RepoFullName

switch ($Status) {
    "ReadyForReview" {
        gh issue edit $issueNumber `
            --repo $repoFullName `
            --remove-label "codex:in-progress" `
            --remove-label "codex:blocked" `
            --add-label "codex:ready-for-review"

        $body = @"
Codex automation completed.

PR:
$($state.PrUrl)

Status:
Ready for review/merge.
"@

        if (-not [string]::IsNullOrWhiteSpace($Message)) {
            $body += @"

Notes:
$Message
"@
        }

        gh issue comment $issueNumber `
            --repo $repoFullName `
            --body $body

        $state.Status = "ReadyForReview"
        Write-State -State $state

        Write-Host "MARKED_READY_FOR_REVIEW"
    }

    "Blocked" {
        gh issue edit $issueNumber `
            --repo $repoFullName `
            --remove-label "codex:in-progress" `
            --add-label "codex:blocked"

        if ([string]::IsNullOrWhiteSpace($Message)) {
            $Message = "Codex automation failed and needs manual review."
        }

        gh issue comment $issueNumber `
            --repo $repoFullName `
            --body @"
Codex automation blocked.

Reason:

~~~
$Message
~~~
"@

        $state.Status = "Blocked"
        Write-State -State $state

        Write-Host "MARKED_BLOCKED"
    }
}
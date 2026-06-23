param(
    [int]$Issue = 0,

    [string]$Username = "",
    [string]$Repo = "",
    [string]$Base = "main",
    [string]$Remote = "origin",

    [string]$Profiles = "",
    [string]$LocalCheck = "",
    [string]$StackContext = "",

    [string]$PromptDir = "$env:USERPROFILE\codex-tools\prompts",
    [string]$ProfilesPath = "$env:USERPROFILE\codex-tools\codex-profiles.json",

    [string]$GitHubTokenSecretName = "",
    [string]$KeePassCliPath = "keepassxc-cli",
    [string]$KeePassDatabasePath = "",
    [string]$KeePassEntryPath = "",
    [string]$KeePassKeyFilePath = "",
    [switch]$KeePassNoPassword,
    [string]$GhConfigDir = "",

    [string]$WorkingDirectory = "",
    [switch]$ForceCurrent
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

function Write-State {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$State,

        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $State |
        ConvertTo-Json -Depth 30 |
        Set-Content -Path $Path -Encoding UTF8
}

function Test-IsIgnoredWorkspacePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $normalized = $RelativePath.Replace("\", "/")

    $ignoredPrefixes = @(
        ".git/",
        ".codex-run/",
        "bin/",
        "obj/",
        "node_modules/",
        "dist/",
        "build/",
        "coverage/",
        ".vs/",
        ".idea/",
        ".vscode/",
        ".venv/",
        "venv/",
        "__pycache__/"
    )

    foreach ($prefix in $ignoredPrefixes) {
        if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }

    if ($normalized -eq "memory.md") {
        return $true
    }

    return $false
}

function Get-WorkspaceSnapshot {
    $root = [System.IO.Path]::GetFullPath(".")
    $snapshot = @{}

    $files = Get-ChildItem -Path . -Recurse -File -Force -ErrorAction SilentlyContinue

    foreach ($file in $files) {
        $relative = [System.IO.Path]::GetRelativePath($root, $file.FullName).Replace("\", "/")

        if (Test-IsIgnoredWorkspacePath -RelativePath $relative) {
            continue
        }

        $hash = Get-FileHash -Path $file.FullName -Algorithm SHA256
        $snapshot[$relative] = $hash.Hash
    }

    return $snapshot
}

function Get-GitHubBaseInfo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoFullName,

        [Parameter(Mandatory = $true)]
        [string]$Base
    )

    $baseRef = gh api "repos/$RepoFullName/git/ref/heads/$Base" | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) {
        throw "Could not read base ref heads/$Base from GitHub."
    }

    $baseSha = [string]$baseRef.object.sha

    $baseCommit = gh api "repos/$RepoFullName/git/commits/$baseSha" | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) {
        throw "Could not read base commit $baseSha from GitHub."
    }

    return @{
        BaseSha = $baseSha
        BaseTreeSha = [string]$baseCommit.tree.sha
    }
}

Set-OptionalWorkingDirectory -Path $WorkingDirectory

Require-Command gh
Require-Command pwsh

Initialize-GitHubToken `
    -GitHubTokenSecretName $GitHubTokenSecretName `
    -KeePassCliPath $KeePassCliPath `
    -KeePassDatabasePath $KeePassDatabasePath `
    -KeePassEntryPath $KeePassEntryPath `
    -KeePassKeyFilePath $KeePassKeyFilePath `
    -KeePassNoPassword:$KeePassNoPassword `
    -GhConfigDir $GhConfigDir

$RepoFullName = Resolve-GitHubRepoFullName -Username $Username -Repo $Repo
Write-Host "Using GitHub repository: $RepoFullName"

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$runRoot = ".codex-run"
$currentDir = Join-Path $runRoot "current"
$statePath = Join-Path $currentDir "state.json"

New-Item -ItemType Directory -Force -Path $runRoot | Out-Null

if (Test-Path $currentDir) {
    if ($ForceCurrent) {
        Remove-Item -Recurse -Force $currentDir
    }
    else {
        $archiveDir = Join-Path $runRoot "archive-$timestamp"
        Move-Item -Path $currentDir -Destination $archiveDir
        Write-Host "Archived previous .codex-run/current to $archiveDir"
    }
}

New-Item -ItemType Directory -Force -Path $currentDir | Out-Null

$issueData = $null
$issueNumber = 0

try {
    if ($Issue -ne 0) {
        $issueData = gh issue view $Issue `
            --repo $RepoFullName `
            --json number,title,body,url,labels | ConvertFrom-Json
    }
    else {
        $issuesJson = gh issue list `
            --repo $RepoFullName `
            --state open `
            --label "codex:ready" `
            --json number,title,labels `
            --limit 50

        $issues = @($issuesJson | ConvertFrom-Json)

        $next = $issues | Where-Object {
            $labelNames = @($_.labels | ForEach-Object { $_.name })
            $labelNames -notcontains "codex:in-progress"
        } | Select-Object -First 1

        if (-not $next) {
            Write-Host "NO_READY_ISSUE"
            exit 0
        }

        $issueData = gh issue view ([int]$next.number) `
            --repo $RepoFullName `
            --json number,title,body,url,labels | ConvertFrom-Json
    }

    $issueNumber = [int]$issueData.number

    Write-Host ("Selected issue #{0}: {1}" -f $issueNumber, $issueData.title)

    gh issue edit $issueNumber `
        --repo $RepoFullName `
        --add-label "codex:in-progress"

    $labelNames = @($issueData.labels | ForEach-Object { $_.name })

    $resolvedProfiles = Resolve-CodexProfiles `
        -Labels $labelNames `
        -Profiles $Profiles `
        -LocalCheck $LocalCheck `
        -StackContext $StackContext `
        -ProfilesPath $ProfilesPath

    $resolvedProfilesCsv = [string]$resolvedProfiles.ProfilesCsv
    $resolvedLocalCheck = [string]$resolvedProfiles.LocalCheck
    $resolvedStackContext = [string]$resolvedProfiles.StackContext

    Write-Host "Using Codex profiles: $resolvedProfilesCsv"
    Write-Host "Using local check: $resolvedLocalCheck"

    $baseInfo = Get-GitHubBaseInfo `
        -RepoFullName $RepoFullName `
        -Base $Base

    $issueTitle = [string]$issueData.title

    $issueText = @"
# GitHub Issue #${issueNumber}: $issueTitle

URL: $($issueData.url)

$($issueData.body)
"@

    $issueLabel = "issue-$issueNumber"
    $branchSlug = Safe-FileName "$issueLabel-$issueTitle"
    $branchName = "codex/$branchSlug-$timestamp"

    $issuePath = Join-Path $currentDir "issue.md"
    Set-Content -Path $issuePath -Encoding UTF8 -Value $issueText

    $snapshot = Get-WorkspaceSnapshot

    $snapshot |
        ConvertTo-Json -Depth 30 |
        Set-Content -Path (Join-Path $currentDir "workspace-snapshot.json") -Encoding UTF8

    $plannerPrompt = New-PromptFromTemplate `
        -PromptDir $PromptDir `
        -TemplateName "planner.md" `
        -Values @{
            IssueText = $issueText
            LocalCheck = $resolvedLocalCheck
            StackContext = $resolvedStackContext
        }

    Set-Content -Path (Join-Path $currentDir "planner.md") -Encoding UTF8 -Value $plannerPrompt

    $state = [ordered]@{
        Status = "Prepared"
        ApiCommitMode = $true

        CreatedAt = (Get-Date).ToString("o")
        Timestamp = $timestamp

        Username = $Username
        Repo = $Repo
        RepoFullName = $RepoFullName

        IssueNumber = $issueNumber
        IssueTitle = $issueTitle
        IssueUrl = [string]$issueData.url
        IssueText = $issueText
        Labels = @($labelNames)

        Base = $Base
        Remote = $Remote
        BranchName = $branchName

        BaseSha = [string]$baseInfo.BaseSha
        BaseTreeSha = [string]$baseInfo.BaseTreeSha
        LastCommitSha = ""

        ProfilesCsv = $resolvedProfilesCsv
        LocalCheck = $resolvedLocalCheck
        StackContext = $resolvedStackContext

        PromptDir = $PromptDir
        ProfilesPath = $ProfilesPath

        RunDir = [System.IO.Path]::GetFullPath($currentDir)

        PrUrl = ""
        PrNumber = 0
        LastLocalCheckPassed = $false

        Auth = [ordered]@{
            GitHubTokenSecretName = $GitHubTokenSecretName
            KeePassCliPath = $KeePassCliPath
            KeePassDatabasePath = $KeePassDatabasePath
            KeePassEntryPath = $KeePassEntryPath
            KeePassKeyFilePath = $KeePassKeyFilePath
            KeePassNoPassword = [bool]$KeePassNoPassword
            GhConfigDir = $GhConfigDir
        }
    }

    Write-State -State $state -Path $statePath

    Write-Host ""
    Write-Host "PREPARED:"
    Write-Host ("Issue: #{0}" -f $issueNumber)
    Write-Host "Branch: $branchName"
    Write-Host "Base SHA: $($baseInfo.BaseSha)"
    Write-Host "Planner prompt: $currentDir\planner.md"
}
catch {
    $message = $_.Exception.Message

    if ($issueNumber -ne 0) {
        try {
            gh issue edit $issueNumber `
                --repo $RepoFullName `
                --remove-label "codex:in-progress" `
                --add-label "codex:blocked"

            gh issue comment $issueNumber `
                --repo $RepoFullName `
                --body @"
Codex automation prepare step failed.

Error:

~~~
$message
~~~
"@
        }
        catch {
            Write-Host "Failed to mark issue as blocked after prepare failure."
        }
    }

    throw
}
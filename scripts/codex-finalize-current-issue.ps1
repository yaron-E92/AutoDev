param(
    [Parameter(Mandatory = $true)]
    [ValidateSet(
        "RenderImplementerPrompt",
        "LocalCheck",
        "PrAndCi",
        "RenderVerificationRepair"
    )]
    [string]$Mode,

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
        ConvertTo-Json -Depth 30 |
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

function Get-ChangedWorkspaceFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SnapshotPath
    )

    if (-not (Test-Path $SnapshotPath)) {
        throw "Missing workspace snapshot: $SnapshotPath"
    }

    $old = Get-Content $SnapshotPath -Raw -Encoding UTF8 | ConvertFrom-Json -AsHashtable
    $current = Get-WorkspaceSnapshot

    $changes = @()

    foreach ($path in $current.Keys) {
        if (-not $old.ContainsKey($path)) {
            $changes += @{
                Path = $path
                Status = "added"
            }
        }
        elseif ([string]$old[$path] -ne [string]$current[$path]) {
            $changes += @{
                Path = $path
                Status = "modified"
            }
        }
    }

    foreach ($path in $old.Keys) {
        if (-not $current.ContainsKey($path)) {
            $changes += @{
                Path = $path
                Status = "deleted"
            }
        }
    }

    return @($changes)
}

function Convert-RepoPathToFullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoPath
    )

    $localPath = $RepoPath.Replace("/", [System.IO.Path]::DirectorySeparatorChar)
    return [System.IO.Path]::GetFullPath((Join-Path "." $localPath))
}

function Get-CodexCommitMessage {
    param(
        [Parameter(Mandatory = $true)]
        $State
    )

    $commitMessagePath = ".codex-run/current/commit-message.txt"

    if (Test-Path $commitMessagePath) {
        $message = Get-Content $commitMessagePath -Raw -Encoding UTF8
        $message = $message.Trim()

        if (-not [string]::IsNullOrWhiteSpace($message)) {
            return $message
        }
    }

    $issueNumber = [int]$State.IssueNumber
    $issueTitle = [string]$State.IssueTitle

    if (-not [string]::IsNullOrWhiteSpace($issueTitle)) {
        return "Implement issue-$issueNumber`: $issueTitle"
    }

    return "Implement issue-$issueNumber via Codex"
}

function New-GitHubApiCommit {
    param(
        [Parameter(Mandatory = $true)]
        $State,

        [Parameter(Mandatory = $true)]
        [array]$Changes,

        [Parameter(Mandatory = $true)]
        [string]$CommitMessage
    )

    $repoFullName = [string]$State.RepoFullName
    $baseTreeSha = [string]$State.BaseTreeSha
    $baseSha = [string]$State.BaseSha
    $branchName = [string]$State.BranchName

    $parentSha = $baseSha

    if (-not [string]::IsNullOrWhiteSpace([string]$State.LastCommitSha)) {
        $parentSha = [string]$State.LastCommitSha
    }

    $treeItems = @()

    foreach ($change in $Changes) {
        $path = [string]$change.Path
        $status = [string]$change.Status

        if ($status -eq "deleted") {
            $treeItems += @{
                path = $path
                sha = $null
            }

            continue
        }

        $fullPath = Convert-RepoPathToFullPath -RepoPath $path

        if (-not (Test-Path $fullPath)) {
            throw "Changed file does not exist: $fullPath"
        }

        $bytes = [System.IO.File]::ReadAllBytes($fullPath)
        $content = [Convert]::ToBase64String($bytes)

        $blobBody = @{
            content = $content
            encoding = "base64"
        } | ConvertTo-Json -Depth 10

        $blob = $blobBody | gh api "repos/$repoFullName/git/blobs" `
            --method POST `
            --input - | ConvertFrom-Json

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create GitHub blob for $path"
        }

        $treeItems += @{
            path = $path
            mode = "100644"
            type = "blob"
            sha = [string]$blob.sha
        }
    }

    if ($treeItems.Count -eq 0) {
        throw "No tree items to commit."
    }

    $treeBody = @{
        base_tree = $baseTreeSha
        tree = $treeItems
    } | ConvertTo-Json -Depth 30

    $tree = $treeBody | gh api "repos/$repoFullName/git/trees" `
        --method POST `
        --input - | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create GitHub tree."
    }

    $commitBody = @{
        message = $CommitMessage
        tree = [string]$tree.sha
        parents = @($parentSha)
    } | ConvertTo-Json -Depth 30

    $commit = $commitBody | gh api "repos/$repoFullName/git/commits" `
        --method POST `
        --input - | ConvertFrom-Json

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create GitHub commit."
    }

    $commitSha = [string]$commit.sha

        $branchRefApiPath = "heads/$branchName"
    $branchRefFullName = "refs/heads/$branchName"

    $existingRefJson = gh api "repos/$repoFullName/git/ref/$branchRefApiPath" 2>$null
    $refExists = ($LASTEXITCODE -eq 0)

    if (-not $refExists) {
        Write-Host "Branch ref does not exist yet. Creating $branchRefFullName"

        $refBody = @{
            ref = $branchRefFullName
            sha = $commitSha
        } | ConvertTo-Json -Depth 10

        $refBody | gh api "repos/$repoFullName/git/refs" `
            --method POST `
            --input - | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create branch ref $branchRefFullName."
        }
    }
    else {
        Write-Host "Branch ref exists. Updating $branchRefFullName"

        $updateBody = @{
            sha = $commitSha
            force = $false
        } | ConvertTo-Json -Depth 10

        $updateBody | gh api "repos/$repoFullName/git/refs/$branchRefApiPath" `
            --method PATCH `
            --input - | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to update branch ref $branchRefFullName."
        }
    }

    return $commitSha
}

function Invoke-LocalCheck {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,

        [Parameter(Mandatory = $true)]
        [string]$LogPath
    )

    Write-Host "Running local check:"
    Write-Host $Command

    try {
        Invoke-Expression "$Command 2>&1" | Tee-Object -FilePath $LogPath
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        $_ | Out-File -FilePath $LogPath -Append
        return $false
    }
}

function Get-PrNumberFromUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PrUrl,

        [Parameter(Mandatory = $true)]
        [string]$RepoFullName
    )

    $json = gh pr view $PrUrl `
        --repo $RepoFullName `
        --json number | ConvertFrom-Json

    return [int]$json.number
}

function Ensure-Pr {
    param($State)

    if (-not [string]::IsNullOrWhiteSpace([string]$State.PrUrl)) {
        Write-Host "PR already known: $($State.PrUrl)"
        return $State
    }

    $prBodyPath = ".codex-run/current/pr-body.md"

    $planText = ""
    if (Test-Path ".codex-run/current/plan.md") {
        $planText = Get-Content ".codex-run/current/plan.md" -Raw -Encoding UTF8
    }

    Set-Content -Path $prBodyPath -Encoding UTF8 -Value @"
Implements:

$($State.IssueText)

Codex plan:

$planText

Local verification:

~~~
$($State.LocalCheck)
~~~
"@

    $prUrl = gh pr create `
        --repo ([string]$State.RepoFullName) `
        --base ([string]$State.Base) `
        --head ([string]$State.BranchName) `
        --title ([string]$State.IssueTitle) `
        --body-file $prBodyPath

    if ($LASTEXITCODE -ne 0) {
        throw "gh pr create failed."
    }

    $State.PrUrl = [string]$prUrl
    $State.PrNumber = Get-PrNumberFromUrl `
        -PrUrl ([string]$State.PrUrl) `
        -RepoFullName ([string]$State.RepoFullName)

    Write-State -State $State

    Write-Host "Created PR:"
    Write-Host $State.PrUrl

    return $State
}

function Watch-PrChecks {
    param($State)

    $prNumber = [int]$State.PrNumber
    $repoFullName = [string]$State.RepoFullName

    if ($prNumber -eq 0) {
        throw "State has no PR number."
    }

    Write-Host ("Watching required PR checks for PR #{0}" -f $prNumber)

    gh pr checks $prNumber `
        --repo $repoFullName `
        --required `
        --watch `
        --fail-fast

    $checksJson = gh pr checks $prNumber `
        --repo $repoFullName `
        --required `
        --json name,bucket,state,description,link

    $checks = @($checksJson | ConvertFrom-Json)

    if (-not $checks -or $checks.Count -eq 0) {
        Write-Host "No required checks found. Treating CI as passed."
        return $true
    }

    $failed = @($checks | Where-Object { $_.bucket -in @("fail", "cancel") })
    $pending = @($checks | Where-Object { $_.bucket -eq "pending" })

    if ($failed.Count -gt 0) {
        $checks |
            ConvertTo-Json -Depth 10 |
            Set-Content -Path ".codex-run/current/ci-summary.json" -Encoding UTF8

        return $false
    }

    if ($pending.Count -gt 0) {
        throw "Checks are still pending."
    }

    return $true
}

function Render-ImplementerPrompt {
    param($State)

    $planPath = ".codex-run/current/plan.md"

    if (-not (Test-Path $planPath)) {
        throw "Cannot render implementer prompt because plan.md is missing."
    }

    $plan = Get-Content $planPath -Raw -Encoding UTF8

    $prompt = New-PromptFromTemplate `
        -PromptDir ([string]$State.PromptDir) `
        -TemplateName "implementer.md" `
        -Values @{
            IssueText = [string]$State.IssueText
            Plan = $plan
            LocalCheck = [string]$State.LocalCheck
            StackContext = [string]$State.StackContext
        }

    Set-Content -Path ".codex-run/current/implementer.md" -Encoding UTF8 -Value $prompt

    $State.Status = "ImplementerPromptRendered"
    Write-State -State $State

    Write-Host "Rendered .codex-run/current/implementer.md"
}

function Render-LocalRepairPrompt {
    param(
        $State,
        [string]$FailureLog
    )

    $prompt = New-PromptFromTemplate `
        -PromptDir ([string]$State.PromptDir) `
        -TemplateName "local-repair.md" `
        -Values @{
            IssueText = [string]$State.IssueText
            FailureLog = $FailureLog
            LocalCheck = [string]$State.LocalCheck
            StackContext = [string]$State.StackContext
        }

    Set-Content -Path ".codex-run/current/local-repair.md" -Encoding UTF8 -Value $prompt
}

function Render-CiRepairPrompt {
    param($State)

    $plan = ""
    if (Test-Path ".codex-run/current/plan.md") {
        $plan = Get-Content ".codex-run/current/plan.md" -Raw -Encoding UTF8
    }

    $ciSummary = ""
    if (Test-Path ".codex-run/current/ci-summary.json") {
        $ciSummary = Get-Content ".codex-run/current/ci-summary.json" -Raw -Encoding UTF8
    }

    $prompt = New-PromptFromTemplate `
        -PromptDir ([string]$State.PromptDir) `
        -TemplateName "ci-repair.md" `
        -Values @{
            IssueText = [string]$State.IssueText
            Plan = $plan
            CiSummary = $ciSummary
            LocalCheck = [string]$State.LocalCheck
            StackContext = [string]$State.StackContext
        }

    Set-Content -Path ".codex-run/current/ci-repair.md" -Encoding UTF8 -Value $prompt
}

function Render-VerifierPrompt {
    param($State)

    $plan = ""
    if (Test-Path ".codex-run/current/plan.md") {
        $plan = Get-Content ".codex-run/current/plan.md" -Raw -Encoding UTF8
    }

    $diff = ""

    if ([int]$State.PrNumber -ne 0) {
        $diff = gh pr diff ([int]$State.PrNumber) `
            --repo ([string]$State.RepoFullName)
    }

    $prompt = New-PromptFromTemplate `
        -PromptDir ([string]$State.PromptDir) `
        -TemplateName "verifier.md" `
        -Values @{
            IssueText = [string]$State.IssueText
            Plan = $plan
            Diff = ($diff | Out-String)
            LocalCheck = [string]$State.LocalCheck
            StackContext = [string]$State.StackContext
        }

    Set-Content -Path ".codex-run/current/verifier.md" -Encoding UTF8 -Value $prompt
}

function Render-VerificationRepairPrompt {
    param($State)

    $verificationPath = ".codex-run/current/verification-result.md"

    if (-not (Test-Path $verificationPath)) {
        throw "Cannot render verification repair prompt because verification-result.md is missing."
    }

    $verification = Get-Content $verificationPath -Raw -Encoding UTF8

    $plan = ""
    if (Test-Path ".codex-run/current/plan.md") {
        $plan = Get-Content ".codex-run/current/plan.md" -Raw -Encoding UTF8
    }

    $prompt = New-PromptFromTemplate `
        -PromptDir ([string]$State.PromptDir) `
        -TemplateName "verification-repair.md" `
        -Values @{
            IssueText = [string]$State.IssueText
            Plan = $plan
            VerificationFailure = $verification
            LocalCheck = [string]$State.LocalCheck
            StackContext = [string]$State.StackContext
        }

    Set-Content -Path ".codex-run/current/verification-repair.md" -Encoding UTF8 -Value $prompt

    $State.Status = "VerificationRepairPromptRendered"
    Write-State -State $State

    Write-Host "Rendered .codex-run/current/verification-repair.md"
}

Set-OptionalWorkingDirectory -Path $WorkingDirectory

Require-Command gh
Require-Command pwsh

$state = Read-State
Initialize-AuthFromState -State $state

switch ($Mode) {
    "RenderImplementerPrompt" {
        Render-ImplementerPrompt -State $state
        exit 0
    }

    "LocalCheck" {
        $logPath = ".codex-run/current/local-check.log"

        $passed = Invoke-LocalCheck `
            -Command ([string]$state.LocalCheck) `
            -LogPath $logPath

        if (-not $passed) {
            $failureLog = Get-Content $logPath -Raw -Encoding UTF8
            Render-LocalRepairPrompt -State $state -FailureLog $failureLog

            $state.Status = "LocalCheckFailed"
            $state.LastLocalCheckPassed = $false
            Write-State -State $state

            Write-Host "LOCAL_CHECK_FAILED"
            exit 10
        }

        $state.Status = "LocalCheckPassed"
        $state.LastLocalCheckPassed = $true
        Write-State -State $state

        Write-Host "LOCAL_CHECK_PASSED"
        exit 0
    }

    "PrAndCi" {
        $changes = @(Get-ChangedWorkspaceFiles -SnapshotPath ".codex-run/current/workspace-snapshot.json")

        if ($changes.Count -eq 0) {
            if ([string]::IsNullOrWhiteSpace([string]$state.PrUrl)) {
                throw "No workspace file changes detected, and no PR exists."
            }

            Write-Host "No new workspace file changes detected. Reusing existing PR."
        }
        else {
            Write-Host "Detected workspace changes:"
            foreach ($change in $changes) {
                Write-Host ("- {0}: {1}" -f $change.Status, $change.Path)
            }

            $commitMessage = Get-CodexCommitMessage -State $state

            Write-Host "Using commit message:"
            Write-Host $commitMessage

            $newCommitSha = New-GitHubApiCommit `
                -State $state `
                -Changes $changes `
                -CommitMessage $commitMessage

            $state.LastCommitSha = $newCommitSha
            $state.Status = "CommittedViaGitHubApi"

            Write-State -State $state

            Write-Host "Created GitHub commit:"
            Write-Host $newCommitSha
        }

        $state = Ensure-Pr -State $state

        $ciPassed = Watch-PrChecks -State $state

        if (-not $ciPassed) {
            Render-CiRepairPrompt -State $state

            $state.Status = "CiFailed"
            Write-State -State $state

            Write-Host "CI_FAILED"
            exit 20
        }

        Render-VerifierPrompt -State $state

        $state.Status = "CiPassedVerifierPromptRendered"
        Write-State -State $state

        Write-Host "CI_PASSED"
        exit 0
    }

    "RenderVerificationRepair" {
        Render-VerificationRepairPrompt -State $state
        exit 0
    }
}
param()

$ErrorActionPreference = 'Stop'

function Write-WorkerResult {
    param(
        [string]$State,
        [string]$CommitSha,
        [string]$SourceIdentity,
        [string]$Reason = '',
        [object[]]$Commands = @()
    )

    [ordered]@{
        version = 1
        state = $State
        platform = 'windows'
        commit_sha = $CommitSha
        source_identity = $SourceIdentity
        reason = $Reason
        commands = $Commands
    } | ConvertTo-Json -Depth 8 -Compress
}

function Invoke-CapturedCommand {
    param(
        [string]$Name,
        [string]$Command
    )

    $tempOutput = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath 'cmd.exe' `
            -ArgumentList @('/d', '/s', '/c', $Command) `
            -NoNewWindow `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $tempOutput `
            -RedirectStandardError ($tempOutput + '.err')
        $stdout = if (Test-Path $tempOutput) { Get-Content -Raw -LiteralPath $tempOutput } else { '' }
        $stderrPath = $tempOutput + '.err'
        $stderr = if (Test-Path $stderrPath) { Get-Content -Raw -LiteralPath $stderrPath } else { '' }
        return [ordered]@{
            name = $Name
            returncode = [int]$process.ExitCode
            output = (($stdout + $stderr) -as [string])
        }
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue $tempOutput, ($tempOutput + '.err')
    }
}

$raw = [Console]::In.ReadToEnd()
$request = $null
$worktree = $null
$commitSha = ''
$sourceIdentity = ''

try {
    if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
        Write-Output (Write-WorkerResult -State 'infrastructure-failure' -CommitSha '' -SourceIdentity '' -Reason 'Windows verification worker is not running on Windows.')
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw 'Windows verification worker received an empty request.'
    }
    $request = $raw | ConvertFrom-Json -Depth 20
    if ([int]$request.version -ne 1) {
        throw 'Unsupported Windows verification request version.'
    }

    $commitSha = [string]$request.commit_sha
    $sourceIdentity = [string]$request.source_identity
    $repositoryUrl = [string]$request.repository_url
    if ([string]::IsNullOrWhiteSpace($commitSha) -or [string]::IsNullOrWhiteSpace($sourceIdentity)) {
        throw 'Windows verification request is missing commit_sha or source_identity.'
    }
    if ([string]::IsNullOrWhiteSpace($repositoryUrl)) {
        throw 'Windows verification request is missing repository_url.'
    }

    $worktree = Join-Path ([System.IO.Path]::GetTempPath()) ('autodev-windows-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $worktree | Out-Null

    & git -C $worktree init --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'git init failed on Windows verification worker.' }
    & git -C $worktree remote add origin $repositoryUrl 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'git remote add failed on Windows verification worker.' }
    $fetchOutput = & git -C $worktree fetch --quiet --depth 1 origin $commitSha 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ('git fetch failed for exact verification commit: ' + (($fetchOutput | Out-String).Trim()))
    }
    & git -C $worktree checkout --quiet --detach FETCH_HEAD 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'git checkout failed on Windows verification worker.' }
    $actual = (& git -C $worktree rev-parse HEAD 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $actual -ne $commitSha) {
        throw "Windows verification checkout identity mismatch: expected $commitSha, got $actual"
    }

    Push-Location $worktree
    try {
        $results = @()
        $failed = $false
        foreach ($item in @($request.commands)) {
            $name = [string]$item.name
            $command = [string]$item.command
            if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($command)) {
                throw 'Windows verification request contains an invalid command entry.'
            }
            $result = Invoke-CapturedCommand -Name $name -Command $command
            $results += $result
            if ([int]$result.returncode -ne 0) {
                $failed = $true
                break
            }
        }
    }
    finally {
        Pop-Location
    }

    $state = if ($failed) { 'code-failure' } else { 'passed' }
    Write-Output (Write-WorkerResult -State $state -CommitSha $commitSha -SourceIdentity $sourceIdentity -Commands $results)
}
catch {
    Write-Output (Write-WorkerResult -State 'infrastructure-failure' -CommitSha $commitSha -SourceIdentity $sourceIdentity -Reason $_.Exception.Message)
}
finally {
    if ($worktree -and (Test-Path -LiteralPath $worktree)) {
        Remove-Item -LiteralPath $worktree -Recurse -Force -ErrorAction SilentlyContinue
    }
}

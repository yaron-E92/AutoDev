[CmdletBinding()]
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

if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
    if (-not (Test-Path -LiteralPath $WorkingDirectory)) {
        throw "Working directory does not exist: $WorkingDirectory"
    }
    Set-Location -LiteralPath $WorkingDirectory
}

Require-Command gh

$statePath = ".codex-run/current/state.json"
if (-not (Test-Path -LiteralPath $statePath)) {
    throw "Missing state file: $statePath"
}
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json

if ($null -ne $state.Auth) {
    Initialize-GitHubToken `
        -GitHubTokenSecretName ([string]$state.Auth.GitHubTokenSecretName) `
        -KeePassCliPath ([string]$state.Auth.KeePassCliPath) `
        -KeePassDatabasePath ([string]$state.Auth.KeePassDatabasePath) `
        -KeePassEntryPath ([string]$state.Auth.KeePassEntryPath) `
        -KeePassKeyFilePath ([string]$state.Auth.KeePassKeyFilePath) `
        -KeePassNoPassword:([bool]$state.Auth.KeePassNoPassword) `
        -GhConfigDir ([string]$state.Auth.GhConfigDir)
}

$scriptRoot = $PSScriptRoot
$toolRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
$python = $(if ($env:PYTHON) { $env:PYTHON } else { "python" })
$oldPythonPath = $env:PYTHONPATH

try {
    $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
        $toolRoot
    }
    else {
        "$toolRoot$([IO.Path]::PathSeparator)$oldPythonPath"
    }

    & $python -m automation.workflow_stage_legacy `
        --mode $Mode `
        --repo ([System.IO.Path]::GetFullPath(".")) `
        --autodev-root $toolRoot
    $code = $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

exit $code

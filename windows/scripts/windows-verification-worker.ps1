param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryPath,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedSha,

    [Parameter(Mandatory = $true)]
    [string]$SourceIdentity,

    [Parameter(Mandatory = $true)]
    [string]$CommandsJson
)

$ErrorActionPreference = 'Stop'

if (-not $IsWindows -and $env:OS -ne 'Windows_NT') {
    throw 'AutoDev Windows verification worker is not running on Windows.'
}

if (-not (Test-Path -LiteralPath $RepositoryPath -PathType Container)) {
    throw "AutoDev Windows verification repository checkout does not exist: $RepositoryPath"
}

$actualSha = (& git -C $RepositoryPath rev-parse HEAD 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw 'AutoDev Windows verification could not resolve the checked-out commit.'
}
if ($actualSha -ne $ExpectedSha) {
    throw "AutoDev Windows verification checkout mismatch: expected $ExpectedSha, got $actualSha"
}

try {
    $commands = @($CommandsJson | ConvertFrom-Json -Depth 20)
}
catch {
    throw "AutoDev Windows verification commands_json is invalid JSON: $($_.Exception.Message)"
}

if ($commands.Count -eq 0) {
    throw 'AutoDev Windows verification received no commands.'
}

Write-Host "AUTODEV_WINDOWS_RUNNER=windows"
Write-Host "AUTODEV_WINDOWS_COMMIT=$ExpectedSha"
Write-Host "AUTODEV_WINDOWS_SOURCE_IDENTITY=$SourceIdentity"

Push-Location $RepositoryPath
try {
    foreach ($item in $commands) {
        $name = [string]$item.name
        $command = [string]$item.command
        if ([string]::IsNullOrWhiteSpace($name) -or [string]::IsNullOrWhiteSpace($command)) {
            throw 'AutoDev Windows verification contains a command without name or command text.'
        }

        Write-Host "::group::AutoDev Windows: $name"
        Write-Host "AUTODEV_WINDOWS_COMMAND_START=$name"
        & cmd.exe /d /s /c $command
        $exitCode = $LASTEXITCODE
        Write-Host "AUTODEV_WINDOWS_COMMAND_END=$name exit=$exitCode"
        Write-Host '::endgroup::'

        if ($exitCode -ne 0) {
            exit $exitCode
        }
    }
}
finally {
    Pop-Location
}

Write-Host 'AUTODEV_WINDOWS_VERIFICATION=passed'
exit 0

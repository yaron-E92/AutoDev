param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$launcher = Get-Command autodev -ErrorAction SilentlyContinue
if ($null -ne $launcher) {
    & $launcher.Source @Arguments
    exit $LASTEXITCODE
}

# Backward compatibility for repositories installed before the first-class
# user-level `autodev` launcher. New repository installs do not create this
# generic configuration under .opencode.
$configPath = Join-Path $PSScriptRoot "autodev.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "AutoDev launcher is not on PATH and no legacy AutoDev OpenCode config exists. Run 'autodev install --user' and 'autodev repo install'."
}

$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$autoDevRoot = [string]$config.autodev_root
$python = if (-not [string]::IsNullOrWhiteSpace([string]$config.python)) { [string]$config.python } elseif ($env:PYTHON) { $env:PYTHON } else { "python" }

if ([string]::IsNullOrWhiteSpace($autoDevRoot) -or -not (Test-Path -LiteralPath $autoDevRoot)) {
    throw "Configured AutoDev root does not exist: $autoDevRoot"
}

$oldPythonPath = $env:PYTHONPATH
$exitCode = 1
try {
    $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
        $autoDevRoot
    }
    else {
        "$autoDevRoot$([IO.Path]::PathSeparator)$oldPythonPath"
    }
    & $python -m automation.autodev_cli @Arguments
    $exitCode = $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

exit $exitCode

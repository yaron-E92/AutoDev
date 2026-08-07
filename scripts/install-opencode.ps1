param(
    [string]$TargetRepository = ".",
    [string]$Python = $(if ($env:PYTHON) { $env:PYTHON } else { "python" })
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$autoDevRoot = Split-Path -Parent $PSScriptRoot
$target = [System.IO.Path]::GetFullPath($TargetRepository)
if (-not (Test-Path -LiteralPath $target -PathType Container)) {
    throw "Target repository does not exist: $target"
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
    & $Python -m automation.opencode_adapter install `
        --target-repo $target `
        --autodev-root $autoDevRoot `
        --python $Python
    $exitCode = $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

exit $exitCode

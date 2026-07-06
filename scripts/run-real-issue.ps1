[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

$ScriptPath = $MyInvocation.MyCommand.Path
$ScriptItem = Get-Item -LiteralPath $ScriptPath
while ($null -ne $ScriptItem.Target) {
    $TargetPath = $ScriptItem.Target
    if (-not [System.IO.Path]::IsPathRooted($TargetPath)) {
        $TargetPath = Join-Path $ScriptItem.DirectoryName $TargetPath
    }
    $ScriptItem = Get-Item -LiteralPath $TargetPath
}
$RepoRoot = (Resolve-Path (Join-Path $ScriptItem.DirectoryName "..")).Path
$LinuxWorkflow = Join-Path $RepoRoot "linux/scripts/issue-to-pr-cycle.sh"

if (Get-Command bash -ErrorAction SilentlyContinue) {
    & bash $LinuxWorkflow @Arguments
    exit $LASTEXITCODE
}

Write-Error "This compatibility wrapper requires bash. Use windows/scripts/codex-*.ps1 directly on systems without bash."
exit 127

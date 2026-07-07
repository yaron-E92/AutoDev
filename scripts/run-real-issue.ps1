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
$WindowsWorkflow = Join-Path $RepoRoot "windows/scripts/issue-to-pr-cycle.ps1"

& pwsh -NoProfile -File $WindowsWorkflow @Arguments
exit $LASTEXITCODE

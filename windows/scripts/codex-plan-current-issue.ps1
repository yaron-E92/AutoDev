param(
    [string]$PlannerAgentCommand = "",
    [string]$WorkingDirectory = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Set-OptionalWorkingDirectory {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Working directory does not exist: $Path"
    }

    Set-Location -LiteralPath $Path
}

function ConvertTo-SingleQuotedShellArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "'\"'\"'") + "'"
}

function Invoke-AgentPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$AgentCommand,
        [Parameter(Mandatory = $true)][string]$Prompt
    )

    $promptFile = New-TemporaryFile
    try {
        Set-Content -LiteralPath $promptFile.FullName -Encoding UTF8 -Value $Prompt

        if ($AgentCommand.Contains("{prompt_file}")) {
            $command = $AgentCommand.Replace("{prompt_file}", (ConvertTo-SingleQuotedShellArgument $promptFile.FullName))
            bash -lc $command
            if ($LASTEXITCODE -ne 0) { throw "Planner agent command failed with exit code $LASTEXITCODE." }
            return
        }

        if ($AgentCommand.Contains("{prompt}")) {
            $command = $AgentCommand.Replace("{prompt}", (ConvertTo-SingleQuotedShellArgument $Prompt))
            bash -lc $command
            if ($LASTEXITCODE -ne 0) { throw "Planner agent command failed with exit code $LASTEXITCODE." }
            return
        }

        $parts = $AgentCommand -split " ", 2
        $exe = $parts[0]
        $prefixArgs = @()
        if ($parts.Count -gt 1 -and -not [string]::IsNullOrWhiteSpace($parts[1])) {
            $prefixArgs = @($parts[1])
        }
        & $exe @prefixArgs $Prompt
        if ($LASTEXITCODE -ne 0) { throw "Planner agent command failed with exit code $LASTEXITCODE." }
    }
    finally {
        Remove-Item -LiteralPath $promptFile.FullName -Force -ErrorAction SilentlyContinue
    }
}

Set-OptionalWorkingDirectory -Path $WorkingDirectory

if ([string]::IsNullOrWhiteSpace($PlannerAgentCommand)) {
    $PlannerAgentCommand = if ([string]::IsNullOrWhiteSpace($env:PLANNER_AGENT_COMMAND)) { "codex exec" } else { $env:PLANNER_AGENT_COMMAND }
}

$currentDir = Join-Path ".codex-run" "current"
$plannerPath = Join-Path $currentDir "planner.md"
$planPath = Join-Path $currentDir "plan.md"

if (-not (Test-Path -LiteralPath $plannerPath)) {
    throw "Missing planner prompt: $plannerPath. Run codex-prepare-next-ready-issue.ps1 first."
}

$plannerPrompt = Get-Content -LiteralPath $plannerPath -Raw -Encoding UTF8
$wrappedPrompt = @"
Use the issue-to-pr-automation skill.

Run the planner prompt below. Write your complete planner output to:

$planPath

Do not edit any other files.

--- PLANNER PROMPT ---
$plannerPrompt
"@

Invoke-AgentPrompt -AgentCommand $PlannerAgentCommand -Prompt $wrappedPrompt

if (-not (Test-Path -LiteralPath $planPath) -or [string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $planPath -Raw -Encoding UTF8))) {
    throw "Planner agent did not write $planPath."
}

Write-Host "PLANNED"
Write-Host "Plan: $planPath"

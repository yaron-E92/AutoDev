[CmdletBinding()]
param(
    [ValidateSet(
        "Run",
        "Plan",
        "Prepare",
        "Preflight",
        "RenderImplementerPrompt",
        "LocalCheck",
        "PrAndCi",
        "RenderVerificationRepair",
        "ReadyForReview",
        "Blocked"
    )]
    [string]$Mode = "Run",

    [int]$Issue = 0,
    [string]$Description = "",
    [string]$DescriptionFile = "",

    [string]$Username = $env:GITHUB_OWNER,
    [string]$Repo = $env:GITHUB_REPO,
    [string]$Base = $(if ($env:BASE_BRANCH) { $env:BASE_BRANCH } else { "main" }),
    [string]$Remote = $(if ($env:REMOTE_NAME) { $env:REMOTE_NAME } else { "origin" }),

    [string]$Profiles = $env:PROFILES,
    [string]$LocalCheck = $env:LOCAL_CHECK,
    [string]$StackContext = $env:STACK_CONTEXT,
    [string]$PromptDir = $(if ($env:PROMPT_DIR) { $env:PROMPT_DIR } else { "$env:USERPROFILE\codex-tools\prompts" }),
    [string]$ProfilesPath = $(if ($env:PROFILES_PATH) { $env:PROFILES_PATH } else { "$env:USERPROFILE\codex-tools\codex-profiles.json" }),
    [string]$ProviderProfile = $env:PROVIDER_PROFILE,
    [string]$ProviderPreflightOut = $(if ($env:PROVIDER_PREFLIGHT_OUT) { $env:PROVIDER_PREFLIGHT_OUT } else { ".codex-run\provider-preflight.json" }),

    [string]$GitHubTokenSecretName = $env:GITHUB_TOKEN_SECRET_NAME,
    [string]$KeePassCliPath = $(if ($env:KEEPASS_CLI) { $env:KEEPASS_CLI } else { "keepassxc-cli" }),
    [string]$KeePassDatabasePath = $env:KEEPASS_DB,
    [string]$KeePassEntryPath = $env:KEEPASS_ENTRY_PATH,
    [string]$KeePassKeyFilePath = $env:KEEPASS_KEY_FILE,
    [switch]$KeePassNoPassword,
    [string]$GhConfigDir = $env:GH_CONFIG_DIR,

    [string]$PlannerAgentCommand = $(if ($env:PLANNER_AGENT_COMMAND) { $env:PLANNER_AGENT_COMMAND } else { "" }),
    [string]$AgentCommand = $(if ($env:AGENT_COMMAND) { $env:AGENT_COMMAND } else { "codex exec" }),
    [string]$PlannerProvider = $env:PLANNER_PROVIDER,
    [string]$PlannerModel = $env:PLANNER_MODEL,
    [string]$AgentProvider = $env:AGENT_PROVIDER,
    [string]$AgentModel = $env:AGENT_MODEL,
    [string]$PromptRunner = $env:PROMPT_RUNNER,
    [string]$Python = $(if ($env:PYTHON) { $env:PYTHON } else { "python" }),
    [int]$MaxRepairAttempts = $(if ($env:MAX_REPAIR_ATTEMPTS) { [int]$env:MAX_REPAIR_ATTEMPTS } else { 3 }),
    [string]$Message = "",
    [string]$WorkingDirectory = "",
    [switch]$ForceCurrent
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = $PSScriptRoot
$toolRoot = Split-Path -Parent (Split-Path -Parent $scriptRoot)
$UsePromptRunnerModule = [string]::IsNullOrWhiteSpace($PromptRunner)
if ([string]::IsNullOrWhiteSpace($PromptRunner)) { $PromptRunner = Join-Path $toolRoot "automation\prompt_runner.py" }
$currentDir = Join-Path ".codex-run" "current"
$telemetryFile = Join-Path $currentDir "model-invocations.json"
$PlannerProviderMode = -not [string]::IsNullOrWhiteSpace($ProviderProfile) -or
    -not [string]::IsNullOrWhiteSpace($PlannerProvider) -or
    -not [string]::IsNullOrWhiteSpace($PlannerModel)
$AgentProviderMode = -not [string]::IsNullOrWhiteSpace($ProviderProfile) -or
    -not [string]::IsNullOrWhiteSpace($AgentProvider) -or
    -not [string]::IsNullOrWhiteSpace($AgentModel)
if ([string]::IsNullOrWhiteSpace($PlannerAgentCommand)) { $PlannerAgentCommand = $AgentCommand }

function Set-OptionalWorkingDirectory {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    if (-not (Test-Path -LiteralPath $Path)) { throw "Working directory does not exist: $Path" }
    Set-Location -LiteralPath $Path
}

function ConvertTo-SingleQuotedPowerShellArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return "'" + $Value.Replace("'", "''") + "'"
}

function Invoke-NativeStep {
    param(
        [Parameter(Mandatory = $true)][string]$Script,
        [string[]]$Arguments = @()
    )
    $output = @(& pwsh -NoProfile -File $Script @Arguments 2>&1)
    $code = $LASTEXITCODE
    foreach ($line in $output) { Write-Host $line }
    return [pscustomobject]@{ Code = $code; Output = @($output | ForEach-Object { [string]$_ }) }
}

function Invoke-PythonModule {
    param(
        [Parameter(Mandatory = $true)][string]$Module,
        [string[]]$Arguments = @()
    )
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($oldPythonPath)) { $toolRoot } else { "$toolRoot$([IO.Path]::PathSeparator)$oldPythonPath" }
        $output = @(& $Python -m $Module @Arguments 2>&1)
        $code = $LASTEXITCODE
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
    }
    foreach ($line in $output) { Write-Host $line }
    return $code
}

function Invoke-AgentPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$FailureMessage
    )
    $promptFile = New-TemporaryFile
    try {
        Set-Content -LiteralPath $promptFile.FullName -Encoding UTF8 -Value $Prompt
        if ($Command.Contains("{prompt_file}")) {
            $rendered = $Command.Replace("{prompt_file}", (ConvertTo-SingleQuotedPowerShellArgument $promptFile.FullName))
        }
        elseif ($Command.Contains("{prompt}")) {
            $rendered = $Command.Replace("{prompt}", (ConvertTo-SingleQuotedPowerShellArgument $Prompt))
        }
        else {
            $rendered = $Command + " " + (ConvertTo-SingleQuotedPowerShellArgument $Prompt)
        }
        pwsh -NoProfile -Command $rendered
        if ($LASTEXITCODE -ne 0) { throw "$FailureMessage Exit code: $LASTEXITCODE." }
    }
    finally {
        Remove-Item -LiteralPath $promptFile.FullName -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-ProviderPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$OutputFile = "",
        [string]$CommitMessageFile = ""
    )
    $promptFile = New-TemporaryFile
    try {
        Set-Content -LiteralPath $promptFile.FullName -Encoding UTF8 -Value $Prompt
        $args = @("--role", $Role, "--prompt-file", $promptFile.FullName, "--telemetry-file", $telemetryFile)
        if (-not [string]::IsNullOrWhiteSpace($ProviderProfile)) { $args += @("--provider-profile", $ProviderProfile) }

        $isPlanner = $Role -eq "planner"
        $legacyProvider = if ($isPlanner) { $PlannerProvider } else { $AgentProvider }
        $legacyModel = if ($isPlanner) { $PlannerModel } else { $AgentModel }
        $legacyCommand = if ($isPlanner) { $PlannerAgentCommand } else { $AgentCommand }
        if (-not [string]::IsNullOrWhiteSpace($legacyProvider)) { $args += @("--provider", $legacyProvider) }
        if (-not [string]::IsNullOrWhiteSpace($legacyModel)) { $args += @("--model", $legacyModel) }
        if ([string]::IsNullOrWhiteSpace($ProviderProfile) -and -not [string]::IsNullOrWhiteSpace($legacyCommand)) { $args += @("--command", $legacyCommand) }
        if (-not [string]::IsNullOrWhiteSpace($OutputFile)) { $args += @("--output-file", $OutputFile) }
        if (-not [string]::IsNullOrWhiteSpace($CommitMessageFile)) { $args += @("--commit-message-file", $CommitMessageFile) }

        if ($UsePromptRunnerModule) {
            $code = Invoke-PythonModule -Module "automation.prompt_runner" -Arguments $args
        }
        else {
            $output = @(& $Python $PromptRunner @args 2>&1)
            $code = $LASTEXITCODE
            foreach ($line in $output) { Write-Host $line }
        }
        if ($code -ne 0) { throw "Provider prompt failed for role $Role. Exit code: $code." }
    }
    finally {
        Remove-Item -LiteralPath $promptFile.FullName -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-ProviderPreflight {
    if ([string]::IsNullOrWhiteSpace($ProviderProfile)) { throw "Preflight requires -ProviderProfile or PROVIDER_PROFILE." }
    $code = Invoke-PythonModule -Module "automation.provider_preflight" -Arguments @(
        "--provider-profile", $ProviderProfile,
        "--out", $ProviderPreflightOut
    )
    if ($code -ne 0) { throw "Provider preflight failed. Exit code: $code." }
    return 0
}

function Get-FileText {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "Missing file: $Path" }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8
}

function Invoke-Prepare {
    if ([string]::IsNullOrWhiteSpace($Username)) { throw "Missing -Username or GITHUB_OWNER." }
    if ([string]::IsNullOrWhiteSpace($Repo)) { throw "Missing -Repo or GITHUB_REPO." }
    $args = @(
        "-Username", $Username,
        "-Repo", $Repo,
        "-Base", $Base,
        "-Remote", $Remote,
        "-PromptDir", $PromptDir,
        "-ProfilesPath", $ProfilesPath
    )
    if ($Issue -ne 0) { $args += @("-Issue", [string]$Issue) }
    if (-not [string]::IsNullOrWhiteSpace($Description)) { $args += @("-Description", $Description) }
    if (-not [string]::IsNullOrWhiteSpace($DescriptionFile)) { $args += @("-DescriptionFile", $DescriptionFile) }
    if (-not [string]::IsNullOrWhiteSpace($Profiles)) { $args += @("-Profiles", $Profiles) }
    if (-not [string]::IsNullOrWhiteSpace($LocalCheck)) { $args += @("-LocalCheck", $LocalCheck) }
    if (-not [string]::IsNullOrWhiteSpace($StackContext)) { $args += @("-StackContext", $StackContext) }
    if (-not [string]::IsNullOrWhiteSpace($GitHubTokenSecretName)) { $args += @("-GitHubTokenSecretName", $GitHubTokenSecretName) }
    if (-not [string]::IsNullOrWhiteSpace($KeePassCliPath)) { $args += @("-KeePassCliPath", $KeePassCliPath) }
    if (-not [string]::IsNullOrWhiteSpace($KeePassDatabasePath)) { $args += @("-KeePassDatabasePath", $KeePassDatabasePath) }
    if (-not [string]::IsNullOrWhiteSpace($KeePassEntryPath)) { $args += @("-KeePassEntryPath", $KeePassEntryPath) }
    if (-not [string]::IsNullOrWhiteSpace($KeePassKeyFilePath)) { $args += @("-KeePassKeyFilePath", $KeePassKeyFilePath) }
    if ($KeePassNoPassword) { $args += "-KeePassNoPassword" }
    if (-not [string]::IsNullOrWhiteSpace($GhConfigDir)) { $args += @("-GhConfigDir", $GhConfigDir) }
    if ($ForceCurrent) { $args += "-ForceCurrent" }
    return Invoke-NativeStep -Script (Join-Path $scriptRoot "codex-prepare-next-ready-issue.ps1") -Arguments $args
}

function Invoke-Finalize {
    param([Parameter(Mandatory = $true)][string]$StepMode)
    return Invoke-NativeStep -Script (Join-Path $scriptRoot "codex-finalize-current-issue.ps1") -Arguments @("-Mode", $StepMode)
}

function Invoke-Mark {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Reason = ""
    )
    $args = @("-Status", $Status)
    if (-not [string]::IsNullOrWhiteSpace($Reason)) { $args += @("-Message", $Reason) }
    Invoke-NativeStep -Script (Join-Path $scriptRoot "codex-mark-current-issue.ps1") -Arguments $args | Out-Null
}

function Invoke-PlanAgent {
    $plannerPath = Join-Path $currentDir "planner.md"
    $planPath = Join-Path $currentDir "plan.md"
    $plannerPrompt = Get-FileText -Path $plannerPath
    if ($PlannerProviderMode) {
        Invoke-ProviderPrompt -Role "planner" -Prompt $plannerPrompt -OutputFile $planPath
    }
    else {
        $prompt = "Use the issue-to-pr-automation skill.`n`nRun the planner prompt below. Write your complete planner output to:`n`n$planPath`n`nDo not edit any other files.`n`n--- PLANNER PROMPT ---`n$plannerPrompt"
        Invoke-AgentPrompt -Command $PlannerAgentCommand -Prompt $prompt -FailureMessage "Planner agent command failed."
    }
    if (-not (Test-Path -LiteralPath $planPath) -or [string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $planPath -Raw -Encoding UTF8))) {
        throw "Planner agent did not write $planPath."
    }
}

function Invoke-ImplementAgent {
    $implementerPath = Join-Path $currentDir "implementer.md"
    $commitMessagePath = Join-Path $currentDir "commit-message.txt"
    $implementerPrompt = Get-FileText -Path $implementerPath
    if ($AgentProviderMode) {
        Invoke-ProviderPrompt -Role "implementer" -Prompt $implementerPrompt -CommitMessageFile $commitMessagePath
    }
    else {
        $prompt = "Use the issue-to-pr-automation skill.`n`nRun the implementer prompt below. Edit the workspace directly.`n`nAlso write a concise commit message to:`n`n$commitMessagePath`n`n--- IMPLEMENTER PROMPT ---`n$implementerPrompt"
        Invoke-AgentPrompt -Command $AgentCommand -Prompt $prompt -FailureMessage "Implementer agent command failed."
    }
    if (-not (Test-Path -LiteralPath $commitMessagePath) -or [string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $commitMessagePath -Raw -Encoding UTF8))) {
        throw "Implementer agent did not write $commitMessagePath."
    }
}

function Invoke-RepairAgent {
    param([Parameter(Mandatory = $true)][string]$PromptPath)
    $repairPrompt = Get-FileText -Path $PromptPath
    if ($AgentProviderMode) {
        Invoke-ProviderPrompt -Role "fixer" -Prompt $repairPrompt
    }
    else {
        $prompt = "Use the issue-to-pr-automation skill.`n`nRun the repair prompt below. Fix only the failure described by the prompt, and edit the workspace directly.`n`n--- REPAIR PROMPT ---`n$repairPrompt"
        Invoke-AgentPrompt -Command $AgentCommand -Prompt $prompt -FailureMessage "Repair agent command failed."
    }
}

function Invoke-VerifyAgent {
    $verifierPath = Join-Path $currentDir "verifier.md"
    $resultPath = Join-Path $currentDir "verification-result.md"
    $verifierPrompt = Get-FileText -Path $verifierPath
    if ($AgentProviderMode) {
        Invoke-ProviderPrompt -Role "verifier" -Prompt $verifierPrompt -OutputFile $resultPath
    }
    else {
        $prompt = "Use the issue-to-pr-automation skill.`n`nRun the verifier prompt below. Write only the verification result to:`n`n$resultPath`n`nThe file must start with exactly PASS or FAIL.`n`n--- VERIFIER PROMPT ---`n$verifierPrompt"
        Invoke-AgentPrompt -Command $AgentCommand -Prompt $prompt -FailureMessage "Verifier agent command failed."
    }
    if (-not (Test-Path -LiteralPath $resultPath) -or [string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8))) {
        throw "Verifier agent did not write $resultPath."
    }
}

function Invoke-PrepareAndPlan {
    $prepare = Invoke-Prepare
    if ($prepare.Output -contains "NO_READY_ISSUE") { return 2 }
    if ($prepare.Code -ne 0) { return $prepare.Code }
    Invoke-PlanAgent
    return 0
}

function Invoke-LocalCheckWithRepairs {
    $attempt = 0
    while ($true) {
        $result = Invoke-Finalize -StepMode "LocalCheck"
        if ($result.Code -eq 0) { return 0 }
        if ($result.Code -ne 10 -or $attempt -ge $MaxRepairAttempts) { return $result.Code }
        $attempt++
        Invoke-RepairAgent -PromptPath (Join-Path $currentDir "local-repair.md")
    }
}

function Invoke-PrAndCiWithRepairs {
    $attempt = 0
    while ($true) {
        $result = Invoke-Finalize -StepMode "PrAndCi"
        if ($result.Code -eq 0) { return 0 }
        if ($result.Code -ne 20 -or $attempt -ge $MaxRepairAttempts) { return $result.Code }
        $attempt++
        Invoke-RepairAgent -PromptPath (Join-Path $currentDir "ci-repair.md")
        $localCode = Invoke-LocalCheckWithRepairs
        if ($localCode -ne 0) { return $localCode }
    }
}

function Invoke-RunCycle {
    $planCode = Invoke-PrepareAndPlan
    if ($planCode -eq 2) { return 0 }
    if ($planCode -ne 0) { return $planCode }
    $render = Invoke-Finalize -StepMode "RenderImplementerPrompt"
    if ($render.Code -ne 0) { return $render.Code }
    try { Invoke-ImplementAgent } catch { Invoke-Mark -Status "Blocked" -Reason "Implementer did not produce commit-message.txt."; throw }
    $localCode = Invoke-LocalCheckWithRepairs
    if ($localCode -ne 0) { Invoke-Mark -Status "Blocked" -Reason "Automation could not complete after local repair attempts."; return $localCode }

    $verificationAttempt = 0
    while ($true) {
        $ciCode = Invoke-PrAndCiWithRepairs
        if ($ciCode -ne 0) { Invoke-Mark -Status "Blocked" -Reason "Automation could not complete after CI repair attempts."; return $ciCode }
        try { Invoke-VerifyAgent } catch { Invoke-Mark -Status "Blocked" -Reason "Verifier did not produce verification-result.md."; throw }
        $firstLine = ((Get-Content -LiteralPath (Join-Path $currentDir "verification-result.md") -TotalCount 1) -replace "`r", "")
        if ($firstLine -eq "PASS") { Invoke-Mark -Status "ReadyForReview"; return 0 }
        if ($firstLine -ne "FAIL") { Invoke-Mark -Status "Blocked" -Reason "Verifier result must start with PASS or FAIL."; return 1 }
        if ($verificationAttempt -ge $MaxRepairAttempts) { Invoke-Mark -Status "Blocked" -Reason "Automation could not complete after verification repair attempts."; return 1 }
        $verificationAttempt++
        $repairRender = Invoke-Finalize -StepMode "RenderVerificationRepair"
        if ($repairRender.Code -ne 0) { return $repairRender.Code }
        Invoke-RepairAgent -PromptPath (Join-Path $currentDir "verification-repair.md")
        $localCode = Invoke-LocalCheckWithRepairs
        if ($localCode -ne 0) { Invoke-Mark -Status "Blocked" -Reason "Automation could not complete after verification local-check repairs."; return $localCode }
    }
}

Set-OptionalWorkingDirectory -Path $WorkingDirectory

switch ($Mode) {
    "Run" { exit (Invoke-RunCycle) }
    "Plan" { $code = Invoke-PrepareAndPlan; if ($code -eq 2) { exit 0 }; exit $code }
    "Prepare" { $result = Invoke-Prepare; Write-Host "NEXT_ACTION: If PREPARED was printed, read $currentDir\planner.md and write $currentDir\plan.md."; exit $result.Code }
    "Preflight" { exit (Invoke-ProviderPreflight) }
    "RenderImplementerPrompt" { $result = Invoke-Finalize -StepMode "RenderImplementerPrompt"; Write-Host "NEXT_ACTION: Read $currentDir\implementer.md, implement directly, and write $currentDir\commit-message.txt."; exit $result.Code }
    "LocalCheck" { $result = Invoke-Finalize -StepMode "LocalCheck"; Write-Host "NEXT_ACTION: If LOCAL_CHECK_FAILED was printed, read $currentDir\local-repair.md and fix only that failure."; exit $result.Code }
    "PrAndCi" { $result = Invoke-Finalize -StepMode "PrAndCi"; Write-Host "NEXT_ACTION: If CI_PASSED was printed, read $currentDir\verifier.md and write $currentDir\verification-result.md. If CI_FAILED was printed, read $currentDir\ci-repair.md."; exit $result.Code }
    "RenderVerificationRepair" { $result = Invoke-Finalize -StepMode "RenderVerificationRepair"; Write-Host "NEXT_ACTION: Read $currentDir\verification-repair.md and fix only verifier gaps."; exit $result.Code }
    "ReadyForReview" { Invoke-Mark -Status "ReadyForReview" -Reason $Message; exit 0 }
    "Blocked" { Invoke-Mark -Status "Blocked" -Reason $(if ($Message) { $Message } else { "Automation could not complete after repair attempts." }); exit 0 }
}

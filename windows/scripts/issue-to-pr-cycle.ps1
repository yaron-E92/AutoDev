[CmdletBinding()]
param(
    [ValidateSet(
        "Run",
        "Plan",
        "Prepare",
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

$PlannerProviderMode = -not [string]::IsNullOrWhiteSpace($PlannerProvider)
$AgentProviderMode = -not [string]::IsNullOrWhiteSpace($AgentProvider)
if (-not [string]::IsNullOrWhiteSpace($PlannerModel)) { $PlannerProviderMode = $true }
if (-not [string]::IsNullOrWhiteSpace($AgentModel)) { $AgentProviderMode = $true }
if (-not [string]::IsNullOrWhiteSpace($PlannerModel) -and [string]::IsNullOrWhiteSpace($PlannerProvider)) { $PlannerProvider = "ollama" }
if (-not [string]::IsNullOrWhiteSpace($AgentModel) -and [string]::IsNullOrWhiteSpace($AgentProvider)) { $AgentProvider = "ollama" }
if ([string]::IsNullOrWhiteSpace($PlannerProvider)) { $PlannerProvider = "command" }
if ([string]::IsNullOrWhiteSpace($AgentProvider)) { $AgentProvider = "command" }
if ($PlannerProvider -notin @("command", "ollama")) { throw "-PlannerProvider must be command or ollama." }
if ($AgentProvider -notin @("command", "ollama")) { throw "-AgentProvider must be command or ollama." }
if ($PlannerProvider -eq "ollama" -and [string]::IsNullOrWhiteSpace($PlannerModel)) { throw "-PlannerProvider ollama requires -PlannerModel." }
if ($AgentProvider -eq "ollama" -and [string]::IsNullOrWhiteSpace($AgentModel)) { throw "-AgentProvider ollama requires -AgentModel." }

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
        [Parameter(Mandatory = $true)][string]$Provider,
        [string]$Model = "",
        [string]$Command = "",
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$OutputFile = "",
        [string]$CommitMessageFile = ""
    )

    $promptFile = New-TemporaryFile
    try {
        Set-Content -LiteralPath $promptFile.FullName -Encoding UTF8 -Value $Prompt
        $args = @($PromptRunner, "--role", $Role, "--provider", $Provider, "--prompt-file", $promptFile.FullName)
        if (-not [string]::IsNullOrWhiteSpace($Model)) { $args += @("--model", $Model) }
        if (-not [string]::IsNullOrWhiteSpace($Command)) { $args += @("--command", $Command) }
        if (-not [string]::IsNullOrWhiteSpace($OutputFile)) { $args += @("--output-file", $OutputFile) }
        if (-not [string]::IsNullOrWhiteSpace($CommitMessageFile)) { $args += @("--commit-message-file", $CommitMessageFile) }

        if ($UsePromptRunnerModule) {
            $oldPythonPath = $env:PYTHONPATH
            try {
                $env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($oldPythonPath)) { $toolRoot } else { "$toolRoot$([IO.Path]::PathSeparator)$oldPythonPath" }
                $output = @(& $Python -m automation.prompt_runner @($args | Select-Object -Skip 1) 2>&1)
                $code = $LASTEXITCODE
            }
            finally {
                $env:PYTHONPATH = $oldPythonPath
            }
        }
        else {
            $output = @(& $Python @args 2>&1)
            $code = $LASTEXITCODE
        }
        foreach ($line in $output) { Write-Host $line }
        if ($code -ne 0) { throw "$Provider provider prompt failed for $Role. Exit code: $code." }
    }
    finally {
        Remove-Item -LiteralPath $promptFile.FullName -Force -ErrorAction SilentlyContinue
    }
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

    Invoke-NativeStep -Script (Join-Path $scriptRoot "codex-prepare-next-ready-issue.ps1") -Arguments $args
}

function Invoke-Finalize {
    param([Parameter(Mandatory = $true)][string]$StepMode)
    Invoke-NativeStep -Script (Join-Path $scriptRoot "codex-finalize-current-issue.ps1") -Arguments @("-Mode", $StepMode)
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
    if ([string]::IsNullOrWhiteSpace($PlannerAgentCommand)) { $PlannerAgentCommand = $AgentCommand }
    $plannerPath = Join-Path $currentDir "planner.md"
    $planPath = Join-Path $currentDir "plan.md"
    $plannerPrompt = Get-FileText -Path $plannerPath

    if ($PlannerProviderMode) {
        $prompt = @"
You are planning an AutoDev issue-to-PR run.

Return only the complete implementation plan as markdown. Do not edit files.

--- PLANNER PROMPT ---
$plannerPrompt
"@
        Invoke-ProviderPrompt -Role "planner" -Provider $PlannerProvider -Model $PlannerModel -Command $PlannerAgentCommand -Prompt $prompt -OutputFile $planPath
    }
    else {
        $prompt = @"
Use the issue-to-pr-automation skill.

Run the planner prompt below. Write your complete planner output to:

$planPath

Do not edit any other files.

--- PLANNER PROMPT ---
$plannerPrompt
"@
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
        $prompt = @"
You are implementing an AutoDev issue-to-PR task as a raw text model.

You cannot edit files directly. Return exactly one of these forms:

NO_CHANGES_REQUIRED

or:

COMMIT_MESSAGE: concise imperative commit message
BEGIN_UNIFIED_DIFF
<unified diff applicable with git apply>
END_UNIFIED_DIFF

--- IMPLEMENTER PROMPT ---
$implementerPrompt
"@
        Invoke-ProviderPrompt -Role "implementer" -Provider $AgentProvider -Model $AgentModel -Command $AgentCommand -Prompt $prompt -CommitMessageFile $commitMessagePath
    }
    else {
        $prompt = @"
Use the issue-to-pr-automation skill.

Run the implementer prompt below. Edit the workspace directly.

Also write a concise commit message to:

$commitMessagePath

Commit message rules:
- One short first line.
- Imperative mood.
- Mention the affected behavior or area.
- No markdown.
- No quotes around the message.

--- IMPLEMENTER PROMPT ---
$implementerPrompt
"@
        Invoke-AgentPrompt -Command $AgentCommand -Prompt $prompt -FailureMessage "Implementer agent command failed."
    }

    if (-not $AgentProviderMode) {
        if (-not (Test-Path -LiteralPath $commitMessagePath) -or [string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $commitMessagePath -Raw -Encoding UTF8))) {
            throw "Implementer agent did not write $commitMessagePath."
        }
    }
}

function Invoke-RepairAgent {
    param([Parameter(Mandatory = $true)][string]$PromptPath)
    $repairPrompt = Get-FileText -Path $PromptPath

    if ($AgentProviderMode) {
        $prompt = @"
You are repairing an AutoDev issue-to-PR task as a raw text model.

You cannot edit files directly. Return exactly one of these forms:

NO_CHANGES_REQUIRED

or:

BEGIN_UNIFIED_DIFF
<unified diff applicable with git apply>
END_UNIFIED_DIFF

--- REPAIR PROMPT ---
$repairPrompt
"@
        Invoke-ProviderPrompt -Role "repair" -Provider $AgentProvider -Model $AgentModel -Command $AgentCommand -Prompt $prompt
    }
    else {
        $prompt = @"
Use the issue-to-pr-automation skill.

Run the repair prompt below. Fix only the failure described by the prompt, and edit the workspace directly.

--- REPAIR PROMPT ---
$repairPrompt
"@
        Invoke-AgentPrompt -Command $AgentCommand -Prompt $prompt -FailureMessage "Repair agent command failed."
    }
}

function Invoke-VerifyAgent {
    $verifierPath = Join-Path $currentDir "verifier.md"
    $resultPath = Join-Path $currentDir "verification-result.md"
    $verifierPrompt = Get-FileText -Path $verifierPath

    if ($AgentProviderMode) {
        $prompt = @"
You are verifying an AutoDev issue-to-PR task.

Return only the verification result. The first line must be exactly PASS or FAIL.

--- VERIFIER PROMPT ---
$verifierPrompt
"@
        Invoke-ProviderPrompt -Role "verifier" -Provider $AgentProvider -Model $AgentModel -Command $AgentCommand -Prompt $prompt -OutputFile $resultPath
    }
    else {
        $prompt = @"
Use the issue-to-pr-automation skill.

Run the verifier prompt below. Write only the verification result to:

$resultPath

The file must start with exactly PASS or FAIL.

--- VERIFIER PROMPT ---
$verifierPrompt
"@
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
        if ($firstLine -eq "PASS") {
            Invoke-Mark -Status "ReadyForReview"
            return 0
        }
        if ($firstLine -ne "FAIL") {
            Invoke-Mark -Status "Blocked" -Reason "Verifier result must start with PASS or FAIL."
            return 1
        }
        if ($verificationAttempt -ge $MaxRepairAttempts) {
            Invoke-Mark -Status "Blocked" -Reason "Automation could not complete after verification repair attempts."
            return 1
        }
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
    "Plan" {
        $code = Invoke-PrepareAndPlan
        if ($code -eq 2) { exit 0 }
        exit $code
    }
    "Prepare" {
        $result = Invoke-Prepare
        Write-Host "NEXT_ACTION: If PREPARED was printed, read $currentDir\planner.md and write $currentDir\plan.md."
        exit $result.Code
    }
    "RenderImplementerPrompt" {
        $result = Invoke-Finalize -StepMode "RenderImplementerPrompt"
        Write-Host "NEXT_ACTION: Read $currentDir\implementer.md, implement directly, and write $currentDir\commit-message.txt."
        exit $result.Code
    }
    "LocalCheck" {
        $result = Invoke-Finalize -StepMode "LocalCheck"
        Write-Host "NEXT_ACTION: If LOCAL_CHECK_FAILED was printed, read $currentDir\local-repair.md and fix only that failure."
        exit $result.Code
    }
    "PrAndCi" {
        $result = Invoke-Finalize -StepMode "PrAndCi"
        Write-Host "NEXT_ACTION: If CI_PASSED was printed, read $currentDir\verifier.md and write $currentDir\verification-result.md. If CI_FAILED was printed, read $currentDir\ci-repair.md."
        exit $result.Code
    }
    "RenderVerificationRepair" {
        $result = Invoke-Finalize -StepMode "RenderVerificationRepair"
        Write-Host "NEXT_ACTION: Read $currentDir\verification-repair.md and fix only verifier gaps."
        exit $result.Code
    }
    "ReadyForReview" { Invoke-Mark -Status "ReadyForReview" -Reason $Message; exit 0 }
    "Blocked" { Invoke-Mark -Status "Blocked" -Reason $(if ($Message) { $Message } else { "Automation could not complete after repair attempts." }); exit 0 }
}

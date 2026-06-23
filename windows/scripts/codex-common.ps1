Set-StrictMode -Version Latest

function Require-Command {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

function Resolve-GitHubRepoFullName {
    param(
        [string]$Username = "",
        [string]$Repo = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($Username) -and
        -not [string]::IsNullOrWhiteSpace($Repo)) {
        return "$Username/$Repo"
    }

    $repoFullName = gh repo view --json nameWithOwner --jq ".nameWithOwner"

    if ([string]::IsNullOrWhiteSpace($repoFullName)) {
        throw "Could not determine GitHub repository. Pass -Username and -Repo explicitly."
    }

    return $repoFullName
}

function Assert-CleanGitTree {
    $status = git status --porcelain

    if ($status) {
        throw "Working tree is not clean. Commit or stash changes first."
    }
}

function Safe-FileName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    $slug = $Text.ToLowerInvariant() -replace '[^a-z0-9]+', '-'
    $slug = $slug.Trim('-')

    if ($slug.Length -gt 70) {
        $slug = $slug.Substring(0, 70).Trim('-')
    }

    return $slug
}

function Get-FirstNonEmptyLine {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return ""
    }

    $lines = $Text -split "`r?`n"

    foreach ($line in $lines) {
        $trimmed = $line.Trim()

        if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
            return $trimmed
        }
    }

    return ""
}

function New-PromptFromTemplate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PromptDir,

        [Parameter(Mandatory = $true)]
        [string]$TemplateName,

        [Parameter(Mandatory = $true)]
        [hashtable]$Values
    )

    $templatePath = Join-Path $PromptDir $TemplateName

    if (-not (Test-Path $templatePath)) {
        throw "Prompt template not found: $templatePath"
    }

    $content = Get-Content -Path $templatePath -Raw -Encoding UTF8

    foreach ($key in $Values.Keys) {
        $placeholder = "{{" + $key + "}}"
        $value = $Values[$key]

        if ($null -eq $value) {
            $value = ""
        }

        $content = $content.Replace($placeholder, [string]$value)
    }

    return $content
}

function Normalize-StackContext {
    param([string]$StackContext = "")

    if (-not [string]::IsNullOrWhiteSpace($StackContext)) {
        return $StackContext.Trim()
    }

    return "Not specified. Use repository files, AGENTS.md, README, project files, solution/package files, and CI configuration as the source of truth."
}

function Get-CodexToolsDir {
    return Join-Path $env:USERPROFILE "codex-tools"
}

function Get-DefaultCodexProfilesPath {
    return Join-Path (Get-CodexToolsDir) "codex-profiles.json"
}

function Expand-CodexProfileTokens {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Text,

        [string]$ProfilesCsv = ""
    )

    $codexToolsDir = Get-CodexToolsDir

    return $Text.
        Replace("{{CodexToolsDir}}", $codexToolsDir).
        Replace("{{ProfilesCsv}}", $ProfilesCsv)
}

function Convert-CodexProfilesArgumentToList {
    param([string]$Profiles = "")

    if ([string]::IsNullOrWhiteSpace($Profiles)) {
        return @()
    }

    $items = @(
        $Profiles -split "[,; ]+" |
            ForEach-Object { $_.Trim().ToLowerInvariant() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Select-Object -Unique
    )

    return @($items)
}

function Resolve-CodexProfiles {
    param(
        [string[]]$Labels = @(),

        [string]$Profiles = "",

        [string]$LocalCheck = "",

        [string]$StackContext = "",

        [string]$ProfilesPath = ""
    )

    if ([string]::IsNullOrWhiteSpace($ProfilesPath)) {
        $ProfilesPath = Get-DefaultCodexProfilesPath
    }

    $resolvedLocalCheck = $LocalCheck
    $resolvedStackContext = $StackContext
    $selectedProfileNames = @(Convert-CodexProfilesArgumentToList -Profiles $Profiles)

    if (-not (Test-Path $ProfilesPath)) {
        if (@($selectedProfileNames).Count -eq 0) {
            $selectedProfileNames = @("auto")
        }

        $profilesCsv = (@($selectedProfileNames) | Select-Object -Unique) -join ","

        if ([string]::IsNullOrWhiteSpace($resolvedLocalCheck)) {
            $resolvedLocalCheck = "pwsh -File `"$((Get-CodexToolsDir))\codex-verify.ps1`" -Profiles `"$profilesCsv`""
        }

        if ([string]::IsNullOrWhiteSpace($resolvedStackContext)) {
            $resolvedStackContext = Normalize-StackContext -StackContext ""
        }

        return @{
            Profiles = @($selectedProfileNames)
            ProfilesCsv = $profilesCsv
            LocalCheck = $resolvedLocalCheck
            StackContext = $resolvedStackContext
        }
    }

    $config = Get-Content $ProfilesPath -Raw -Encoding UTF8 | ConvertFrom-Json

    if (@($selectedProfileNames).Count -eq 0) {
        foreach ($profileProperty in $config.profiles.PSObject.Properties) {
            $candidateName = $profileProperty.Name
            $candidate = $profileProperty.Value

            foreach ($label in $candidate.labels) {
                if ($Labels -contains $label) {
                    $selectedProfileNames += $candidateName
                    break
                }
            }
        }

        $selectedProfileNames = @($selectedProfileNames | Select-Object -Unique)
    }

    if (@($selectedProfileNames).Count -eq 0) {
        if (-not [string]::IsNullOrWhiteSpace([string]$config.defaultProfile)) {
            $selectedProfileNames = @([string]$config.defaultProfile)
        }
        else {
            $selectedProfileNames = @("auto")
        }
    }

    if ($selectedProfileNames -contains "auto" -and @($selectedProfileNames).Count -gt 1) {
        $selectedProfileNames = @($selectedProfileNames | Where-Object { $_ -ne "auto" })
    }

    $verifyProfiles = @()
    $stackContextParts = @()

    foreach ($profileName in $selectedProfileNames) {
        if ($profileName -eq "auto") {
            $verifyProfiles += "auto"
            $stackContextParts += "No specific area profile was selected. Use repository AGENTS.md, README, project files, solution/package files, and CI configuration as the source of truth. Prefer the smallest safe scope."
            continue
        }

        if (-not ($config.profiles.PSObject.Properties.Name -contains $profileName)) {
            throw "Codex profile '$profileName' was not found in $ProfilesPath."
        }

        $profileConfig = $config.profiles.$profileName

        if (-not [string]::IsNullOrWhiteSpace([string]$profileConfig.verifyProfile)) {
            $verifyProfiles += [string]$profileConfig.verifyProfile
        }
        else {
            $verifyProfiles += $profileName
        }

        if (-not [string]::IsNullOrWhiteSpace([string]$profileConfig.stackContext)) {
            $stackContextParts += [string]$profileConfig.stackContext
        }
    }

    $verifyProfiles = @($verifyProfiles | Select-Object -Unique)
    $profilesCsv = $verifyProfiles -join ","

    if ([string]::IsNullOrWhiteSpace($resolvedLocalCheck)) {
        $template = [string]$config.verifyCommandTemplate

        if ([string]::IsNullOrWhiteSpace($template)) {
            $template = "pwsh -File `"{{CodexToolsDir}}\codex-verify.ps1`" -Profiles `"{{ProfilesCsv}}`""
        }

        $resolvedLocalCheck = Expand-CodexProfileTokens `
            -Text $template `
            -ProfilesCsv $profilesCsv
    }

    if ([string]::IsNullOrWhiteSpace($resolvedStackContext)) {
        $resolvedStackContext = ($stackContextParts | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        }) -join "`n"
    }

    if ([string]::IsNullOrWhiteSpace($resolvedStackContext)) {
        $resolvedStackContext = Normalize-StackContext -StackContext ""
    }

    return @{
        Profiles = @($selectedProfileNames)
        ProfilesCsv = $profilesCsv
        LocalCheck = $resolvedLocalCheck
        StackContext = $resolvedStackContext
    }
}

function Initialize-GitHubCliEnvironment {
    param(
        [string]$GhConfigDir = ""
    )

    if ([string]::IsNullOrWhiteSpace($GhConfigDir)) {
        $GhConfigDir = Join-Path (Get-Location) ".codex-run\gh-config"
    }

    $GhConfigDir = [System.IO.Path]::GetFullPath($GhConfigDir)

    New-Item -ItemType Directory -Force -Path $GhConfigDir | Out-Null

    $env:GH_CONFIG_DIR = $GhConfigDir
    $env:GH_PROMPT_DISABLED = "1"

    Write-Host "Using GH_CONFIG_DIR: $GhConfigDir"
}

function Use-GitHubTokenSecret {
    param(
        [string]$GitHubTokenSecretName = ""
    )

    if ([string]::IsNullOrWhiteSpace($GitHubTokenSecretName)) {
        return
    }

    if (-not (Get-Command Get-Secret -ErrorAction SilentlyContinue)) {
        throw "Get-Secret is not available. Install Microsoft.PowerShell.SecretManagement and Microsoft.PowerShell.SecretStore, or omit -GitHubTokenSecretName."
    }

    $token = Get-Secret -Name $GitHubTokenSecretName -AsPlainText

    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "GitHub token secret '$GitHubTokenSecretName' was not found or was empty."
    }

    $env:GH_TOKEN = $token

    Write-Host "Loaded GitHub token from secret '$GitHubTokenSecretName' into GH_TOKEN for this process."
}

function Use-GitHubTokenFromKeePassXC {
    param(
        [string]$KeePassCliPath = "keepassxc-cli",
        [string]$KeePassDatabasePath = "",
        [string]$KeePassEntryPath = "",
        [string]$KeePassKeyFilePath = "",
        [switch]$KeePassNoPassword
    )

    if ([string]::IsNullOrWhiteSpace($KeePassDatabasePath) -or
        [string]::IsNullOrWhiteSpace($KeePassEntryPath)) {
        return $false
    }

    if (-not (Get-Command $KeePassCliPath -ErrorAction SilentlyContinue) -and
        -not (Test-Path $KeePassCliPath)) {
        throw "KeePassXC CLI not found: $KeePassCliPath. Pass -KeePassCliPath with the full path to keepassxc-cli.exe."
    }

    if (-not (Test-Path $KeePassDatabasePath)) {
        throw "KeePassXC database not found: $KeePassDatabasePath"
    }

    $args = @("show", "--show-protected", "--attributes", "Password")

    if ($KeePassNoPassword) {
        $args += "--no-password"
    }

    if (-not [string]::IsNullOrWhiteSpace($KeePassKeyFilePath)) {
        if (-not (Test-Path $KeePassKeyFilePath)) {
            throw "KeePassXC key file not found: $KeePassKeyFilePath"
        }

        $args += @("--key-file", $KeePassKeyFilePath)
    }

    $args += @($KeePassDatabasePath, $KeePassEntryPath)

    $token = & $KeePassCliPath @args

    if ($LASTEXITCODE -ne 0) {
        throw "keepassxc-cli failed to retrieve entry '$KeePassEntryPath'."
    }

    $token = ($token | Out-String).Trim()

    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "KeePassXC entry '$KeePassEntryPath' returned an empty token."
    }

    $env:GH_TOKEN = $token
    Write-Host "Loaded GitHub token from KeePassXC entry '$KeePassEntryPath' into GH_TOKEN for this process."
    return $true
}

function Initialize-GitHubToken {
    param(
        [string]$GitHubTokenSecretName = "",
        [string]$KeePassCliPath = "keepassxc-cli",
        [string]$KeePassDatabasePath = "",
        [string]$KeePassEntryPath = "",
        [string]$KeePassKeyFilePath = "",
        [switch]$KeePassNoPassword,
        [string]$GhConfigDir = ""
    )

    Initialize-GitHubCliEnvironment -GhConfigDir $GhConfigDir

    if (-not [string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
        Write-Host "GH_TOKEN is already set for this process."
        return
    }

    if (Use-GitHubTokenSecret -GitHubTokenSecretName $GitHubTokenSecretName) {
        return
    }

    if (Use-GitHubTokenFromKeePassXC `
            -KeePassCliPath $KeePassCliPath `
            -KeePassDatabasePath $KeePassDatabasePath `
            -KeePassEntryPath $KeePassEntryPath `
            -KeePassKeyFilePath $KeePassKeyFilePath `
            -KeePassNoPassword:$KeePassNoPassword) {
        return
    }

    Write-Host "No GH_TOKEN source configured. Falling back to existing gh authentication, if available."
}
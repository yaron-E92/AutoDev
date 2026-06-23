param(
    [Parameter(Mandatory = $true)]
    [string]$Username,

    [Parameter(Mandatory = $true)]
    [string]$Repo,

    [switch]$IncludeAreaLabels
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. "$env:USERPROFILE\codex-tools\codex-common.ps1"

Require-Command gh

$repoTarget = Resolve-GitHubRepoFullName -Username $Username -Repo $Repo

$labels = @(
    @{
        Name = "codex:ready"
        Color = "0E8A16"
        Description = "Ready for Codex automation"
    },
    @{
        Name = "codex:in-progress"
        Color = "FBCA04"
        Description = "Codex is currently working on this"
    },
    @{
        Name = "codex:ready-for-review"
        Color = "1D76DB"
        Description = "Codex says the PR is ready to review or merge"
    },
    @{
        Name = "codex:blocked"
        Color = "D93F0B"
        Description = "Codex automation failed or needs human input"
    }
)

if ($IncludeAreaLabels) {
    $labels += @(
        @{
            Name = "area:backend"
            Color = "5319E7"
            Description = "Backend/API issue"
        },
        @{
            Name = "area:web"
            Color = "1D76DB"
            Description = "React/Vite web issue"
        },
        @{
            Name = "area:maui"
            Color = "FBCA04"
            Description = "MAUI client issue"
        },
        @{
            Name = "area:python"
            Color = "2EA44F"
            Description = "Python issue"
        }
    )
}

foreach ($label in $labels) {
    Write-Host "Creating/updating label '$($label.Name)' in $repoTarget"

    gh label create $label.Name `
        --repo $repoTarget `
        --color $label.Color `
        --description $label.Description `
        --force
}

Write-Host "Codex labels are ready in $repoTarget"
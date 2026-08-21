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
        Name = "autodev:managed"
        Color = "1D76DB"
        Description = "Human authorization for autonomous AutoDev work"
    },
    @{
        Name = "autodev:ready"
        Color = "0E8A16"
        Description = "Derived: managed and currently runnable by AutoDev"
    },
    @{
        Name = "autodev:blocked"
        Color = "D93F0B"
        Description = "Derived: managed but blocked by open issue dependencies"
    },
    @{
        Name = "autodev:attention"
        Color = "FBCA04"
        Description = "Human attention is required before autonomous AutoDev work"
    },
    @{
        Name = "autodev:running"
        Color = "5319E7"
        Description = "Active AutoDev claim/run for this issue"
    },
    @{
        Name = "autodev:failed"
        Color = "D93F0B"
        Description = "AutoDev automation failed"
    },
    @{
        Name = "autodev:done"
        Color = "5319E7"
        Description = "AutoDev completed this issue"
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

Write-Host "AutoDev labels are ready in $repoTarget"
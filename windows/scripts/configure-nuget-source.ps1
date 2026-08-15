[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SourceUrl,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$SourceName,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Username,

    [string]$ConfigFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:NUGET_TOKEN)) {
    throw "NUGET_TOKEN is required to configure the authenticated NuGet source."
}

[Uri]$parsedSourceUrl = $null
if (
    -not [Uri]::TryCreate($SourceUrl, [UriKind]::Absolute, [ref]$parsedSourceUrl) -or
    $parsedSourceUrl.Scheme -ne [Uri]::UriSchemeHttps
) {
    throw "SourceUrl must be an absolute HTTPS URL."
}

$dotnet = Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $dotnet) {
    throw "dotnet is required to configure the authenticated NuGet source."
}

$configArguments = @()
if (-not [string]::IsNullOrWhiteSpace($ConfigFile)) {
    $resolvedConfigFile = [IO.Path]::GetFullPath($ConfigFile)
    if (-not (Test-Path -LiteralPath $resolvedConfigFile -PathType Leaf)) {
        throw "ConfigFile does not exist: $resolvedConfigFile"
    }
    $configArguments = @("--configfile", $resolvedConfigFile)
}

# Hosted runners are normally clean, but replacing the named source makes the helper
# deterministic on reused runners as well. A missing source is intentionally harmless.
$removeArguments = @("nuget", "remove", "source", $SourceName) + $configArguments
& $dotnet.Source @removeArguments *> $null

$addArguments = @(
    "nuget", "add", "source", $parsedSourceUrl.AbsoluteUri,
    "--name", $SourceName,
    "--username", $Username,
    "--password", $env:NUGET_TOKEN,
    "--store-password-in-clear-text",
    "--valid-authentication-types", "basic"
) + $configArguments
& $dotnet.Source @addArguments

if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure authenticated NuGet source '$SourceName'."
}

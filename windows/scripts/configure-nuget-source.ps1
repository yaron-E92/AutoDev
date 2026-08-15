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
    [string]$Username
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

$dotnet = Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue
if ($null -eq $dotnet) {
    throw "dotnet is required to configure the authenticated NuGet source."
}

# Hosted runners are normally clean, but replacing the named source makes the helper
# deterministic on reused runners as well. A missing source is intentionally harmless.
& $dotnet.Source nuget remove source $SourceName *> $null

& $dotnet.Source nuget add source $parsedSourceUrl.AbsoluteUri `
    --name $SourceName `
    --username $Username `
    --password $env:NUGET_TOKEN `
    --store-password-in-clear-text `
    --valid-authentication-types basic

if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure authenticated NuGet source '$SourceName'."
}

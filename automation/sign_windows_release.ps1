param(
    [Parameter(Mandatory = $true)]
    [string] $InputMsi,

    [string] $OutputMsi = '',

    [switch] $VerifyOnly,

    [string] $TimestampUrl = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true

$codeSigningEku = '1.3.6.1.5.5.7.3.3'
$pfxBase64 = $env:AUTODEV_WINDOWS_SIGNING_PFX_BASE64
$pfxPassword = $env:AUTODEV_WINDOWS_SIGNING_PFX_PASSWORD

if ([string]::IsNullOrWhiteSpace($pfxBase64)) {
    throw 'Missing protected secret AUTODEV_WINDOWS_SIGNING_PFX_BASE64.'
}
if ([string]::IsNullOrWhiteSpace($pfxPassword)) {
    throw 'Missing protected secret AUTODEV_WINDOWS_SIGNING_PFX_PASSWORD.'
}
if (-not (Test-Path -LiteralPath $InputMsi -PathType Leaf)) {
    throw "MSI does not exist: $InputMsi"
}
if (-not $VerifyOnly -and [string]::IsNullOrWhiteSpace($OutputMsi)) {
    throw 'OutputMsi is required when signing a new MSI.'
}

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $candidate = Get-ChildItem -Path $kits -Filter signtool.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.DirectoryName -like '*\x64' } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $candidate) {
        throw 'signtool.exe is unavailable on the Windows release runner.'
    }
    return $candidate.FullName
}

$tempRoot = if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    [IO.Path]::GetTempPath()
}
else {
    $env:RUNNER_TEMP
}

$work = Join-Path $tempRoot ('autodev-signing-' + [guid]::NewGuid().ToString('N'))

New-Item -ItemType Directory -Path $work -Force | Out-Null
$pfxPath = Join-Path $work 'autodev-signing.pfx'
$cerPath = Join-Path $work 'autodev-signing.cer'
$importedMy = @()
$signer = $null
$addedRootTrust = $false

try {
    try {
        [IO.File]::WriteAllBytes($pfxPath, [Convert]::FromBase64String($pfxBase64))
    }
    catch {
        throw 'AUTODEV_WINDOWS_SIGNING_PFX_BASE64 is not valid base64 PFX content.'
    }

    $securePassword = ConvertTo-SecureString -String $pfxPassword -AsPlainText -Force
    $importedMy = @(Import-PfxCertificate `
        -FilePath $pfxPath `
        -Password $securePassword `
        -CertStoreLocation 'Cert:\CurrentUser\My')

    $signers = @(
        $importedMy | Where-Object {
            $_.HasPrivateKey -and
            @($_.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId }) -contains $codeSigningEku
        }
    )
    if ($signers.Count -ne 1) {
        throw "Expected exactly one code-signing certificate with a private key in the PFX; found $($signers.Count)."
    }
    $signer = $signers[0]

    Export-Certificate -Cert $signer -FilePath $cerPath -Force | Out-Null
    $rootCertificatePath = "Cert:\CurrentUser\Root\$($signer.Thumbprint)"
    if (-not (Test-Path -LiteralPath $rootCertificatePath)) {
        & certutil.exe -user -addstore -f Root $cerPath
        $addedRootTrust = $true
    }

    $targetMsi = $InputMsi
    $signTool = Find-SignTool
    if (-not $VerifyOnly) {
        $destinationParent = Split-Path -Parent $OutputMsi
        if (-not [string]::IsNullOrWhiteSpace($destinationParent)) {
            New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
        }
        Copy-Item -LiteralPath $InputMsi -Destination $OutputMsi -Force
        $targetMsi = $OutputMsi

        & $signTool sign `
            /fd SHA256 `
            /tr $TimestampUrl `
            /td SHA256 `
            /s My `
            /sha1 $signer.Thumbprint `
            $targetMsi
    }

    & $signTool verify /pa /all /v $targetMsi

    $signature = Get-AuthenticodeSignature -FilePath $targetMsi
    if ($signature.Status -ne 'Valid') {
        throw "Signed MSI did not pass Authenticode verification: $($signature.Status) $($signature.StatusMessage)"
    }
    if ($null -eq $signature.SignerCertificate) {
        throw 'Signed MSI does not expose a signer certificate.'
    }
    if ($signature.SignerCertificate.Thumbprint -ne $signer.Thumbprint) {
        throw "Signed MSI signer thumbprint $($signature.SignerCertificate.Thumbprint) does not match the protected PFX signer $($signer.Thumbprint)."
    }
    if ($null -eq $signature.TimeStamperCertificate) {
        throw 'Signed MSI does not contain a trusted RFC 3161 timestamp.'
    }

    Write-Host "Verified signed MSI: $targetMsi"
    Write-Host "Signer subject: $($signer.Subject)"
    Write-Host "Signer thumbprint: $($signer.Thumbprint)"
    Write-Host "Timestamp authority: $($signature.TimeStamperCertificate.Subject)"
}
finally {
    if ($addedRootTrust -and $null -ne $signer) {
        & certutil.exe -user -delstore Root $signer.Thumbprint
    }
    foreach ($certificate in $importedMy) {
        Remove-Item -LiteralPath $certificate.PSPath -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $work) {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

param(
  [Parameter(Mandatory = $true)]
  [string]$Path,

  [switch]$AllowUnsigned
)

$ErrorActionPreference = "Stop"

if (!(Test-Path $Path)) {
  throw "File was not found: $Path"
}

$signature = Get-AuthenticodeSignature -FilePath $Path

if ($signature.Status -eq "Valid") {
  Write-Host "[codesign] Signature valid: $Path"
  if ($signature.SignerCertificate) {
    Write-Host "[codesign] Signer: $($signature.SignerCertificate.Subject)"
  }
  exit 0
}

if ($AllowUnsigned -and $signature.Status -eq "NotSigned") {
  Write-Host "[codesign] File is unsigned; allowed for development build: $Path"
  exit 0
}

Write-Host "[codesign] Signature status: $($signature.Status)"
Write-Host "[codesign] Status message: $($signature.StatusMessage)"
throw "Signature verification failed: $Path"

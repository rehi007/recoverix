param(
  [Parameter(Mandatory = $true)]
  [string]$InputPath,

  [Parameter(Mandatory = $true)]
  [string]$OutputPath,

  [switch]$Optional
)

$ErrorActionPreference = "Stop"

function Resolve-Tool {
  param([string]$Name)
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($null -eq $cmd) {
    return $null
  }
  return $cmd.Source
}

function Find-SignTool {
  $direct = Resolve-Tool "signtool.exe"
  if ($direct) {
    return $direct
  }

  $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
  if (Test-Path $kitsRoot) {
    $candidate = Get-ChildItem -Path $kitsRoot -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
      Sort-Object FullName -Descending |
      Select-Object -First 1
    if ($candidate) {
      return $candidate.FullName
    }
  }
  return $null
}

if (!(Test-Path $InputPath)) {
  throw "Input executable was not found: $InputPath"
}

$outputDir = Split-Path -Parent $OutputPath
if ($outputDir -and !(Test-Path $outputDir)) {
  New-Item -ItemType Directory -Path $outputDir | Out-Null
}
Copy-Item -Path $InputPath -Destination $OutputPath -Force

$pfx = $env:RECOVERIX_CODESIGN_PFX
$password = $env:RECOVERIX_CODESIGN_PASSWORD
$timestampUrl = if ($env:RECOVERIX_TIMESTAMP_URL) {
  $env:RECOVERIX_TIMESTAMP_URL
} else {
  "http://timestamp.digicert.com"
}

if ([string]::IsNullOrWhiteSpace($pfx) -or [string]::IsNullOrWhiteSpace($password)) {
  if ($Optional) {
    Write-Host "[codesign] RECOVERIX_CODESIGN_PFX or RECOVERIX_CODESIGN_PASSWORD is not set."
    Write-Host "[codesign] Unsigned development build copied to $OutputPath"
    exit 0
  }
  throw "Code signing certificate environment variables are not set."
}

if (!(Test-Path $pfx)) {
  throw "Code signing PFX was not found: $pfx"
}

$signtool = Find-SignTool
if (!$signtool) {
  if ($Optional) {
    Write-Host "[codesign] signtool.exe was not found. Unsigned development build copied to $OutputPath"
    exit 0
  }
  throw "signtool.exe was not found. Install Windows SDK or add signtool.exe to PATH."
}

& $signtool sign `
  /f "$pfx" `
  /p "$password" `
  /fd SHA256 `
  /tr "$timestampUrl" `
  /td SHA256 `
  "$OutputPath"

if ($LASTEXITCODE -ne 0) {
  throw "signtool sign failed with exit code $LASTEXITCODE"
}

Write-Host "[codesign] Signed: $OutputPath"

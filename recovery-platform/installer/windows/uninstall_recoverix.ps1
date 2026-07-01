$ErrorActionPreference = 'Stop'

param(
    [switch]$KeepData
)

function Assert-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Recoverix uninstaller must be run as Administrator."
    }
}

try {
    Assert-Administrator

    schtasks /Query /TN RecoveryBootMonitor >$null 2>$null
    if ($LASTEXITCODE -eq 0) {
        schtasks /Delete /TN RecoveryBootMonitor /F | Out-Null
    }

    $installRoot = Join-Path $env:ProgramFiles "Recoverix"
    if (Test-Path $installRoot) {
        Remove-Item -LiteralPath $installRoot -Recurse -Force
    }

    if (-not $KeepData) {
        $dataRoot = Join-Path $env:ProgramData "Recoverix"
        if (Test-Path $dataRoot) {
            Remove-Item -LiteralPath $dataRoot -Recurse -Force
        }
    }

    Write-Host "Recoverix Windows components removed."
    Write-Host "EFI entries, NVRAM entries, and recovery partitions are not removed by this script."
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

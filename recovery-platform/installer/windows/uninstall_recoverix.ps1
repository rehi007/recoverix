param(
    [switch]$KeepData,
    [string]$InstallRoot = ""
)

$ErrorActionPreference = 'Stop'
$UninstallRegistryPath = "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix"
$LegacyUninstallRegistryPaths = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix Status",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus"
)
$ShortcutNames = @(
    ("Recoverix " + [string][char]0xC0C1 + [string][char]0xD0DC + " " + [string][char]0xD655 + [string][char]0xC778 + ".lnk"),
    "Recoverix Status.lnk",
    "RecoverixStatus.lnk"
)

function Write-UninstallLog($Message) {
    $logPath = Join-Path $env:TEMP "RecoverixUninstall-T.1.0.0.log"
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Encoding UTF8 -Path $logPath -Value $line
}

function Assert-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Recoverix uninstaller must be run as Administrator."
    }
}

function Resolve-RecoverixInstallRoot {
    param([string]$Root)

    if (-not [string]::IsNullOrWhiteSpace($Root)) {
        $cleanRoot = $Root.Trim().Trim('"')
        if (Test-Path -LiteralPath $cleanRoot) {
            return (Resolve-Path -LiteralPath $cleanRoot).Path
        }
        return $cleanRoot
    }

    return (Join-Path $env:ProgramFiles "Recoverix")
}

function Remove-RecoverixRegistryEntries {
    foreach ($path in @($UninstallRegistryPath) + $LegacyUninstallRegistryPaths) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
            Write-UninstallLog "Removed uninstall registry entry: $path"
        }
    }
}

function Remove-DirectoryWithRetry {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-UninstallLog "Directory already absent: $Path"
        return $true
    }

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        } catch {
            Write-UninstallLog ("Remove attempt {0} failed for {1}: {2}" -f $attempt, $Path, $_.Exception.Message)
        }

        if (-not (Test-Path -LiteralPath $Path)) {
            Write-UninstallLog "Removed directory: $Path"
            return $true
        }
        Start-Sleep -Seconds 1
    }

    Write-UninstallLog "WARNING: Failed to remove directory after retries: $Path"
    return $false
}

function Reset-RecoverixDirectoryAccess {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    Write-UninstallLog "Resetting Recoverix directory permissions: $Path"
    try {
        & takeown.exe /F $Path /R /D Y | Out-Null
        Write-UninstallLog "takeown rc=$LASTEXITCODE for $Path"
    } catch {
        Write-UninstallLog "takeown exception for $Path : $($_.Exception.Message)"
    }

    try {
        & icacls.exe $Path /inheritance:e /T /C | Out-Null
        Write-UninstallLog "icacls inheritance rc=$LASTEXITCODE for $Path"
    } catch {
        Write-UninstallLog "icacls inheritance exception for $Path : $($_.Exception.Message)"
    }

    try {
        & icacls.exe $Path /grant "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" /T /C | Out-Null
        Write-UninstallLog "icacls grant rc=$LASTEXITCODE for $Path"
    } catch {
        Write-UninstallLog "icacls grant exception for $Path : $($_.Exception.Message)"
    }
}

function Remove-RecoverixScheduledTask {
    try {
        $output = & schtasks.exe /Delete /TN RecoveryBootMonitor /F 2>&1
        $deleteExitCode = $LASTEXITCODE
        if ($deleteExitCode -eq 0) {
            Write-UninstallLog "Removed scheduled task: RecoveryBootMonitor"
        } else {
            Write-UninstallLog "Scheduled task removal skipped or failed with rc=$deleteExitCode. Output: $($output -join ' ')"
        }
    } catch {
        Write-UninstallLog "Scheduled task removal skipped after exception: $($_.Exception.Message)"
    }
}

function Stop-RecoverixProcesses {
    foreach ($name in @("RecoverixStatus")) {
        $processes = Get-Process -Name $name -ErrorAction SilentlyContinue
        foreach ($process in $processes) {
            try {
                Write-UninstallLog "Stopping process: $($process.ProcessName) pid=$($process.Id)"
                Stop-Process -Id $process.Id -Force -ErrorAction Stop
            } catch {
                Write-UninstallLog "WARNING: Could not stop process $name pid=$($process.Id): $($_.Exception.Message)"
            }
        }
    }
}

function Confirm-RecoverixUninstall {
    $message = @"
Recoverix Windows 구성요소를 제거하시겠습니까?

삭제되는 항목:
- Windows 상태확인 프로그램
- Windows Agent
- RecoveryBootMonitor 작업 스케줄러
- C:\Program Files\Recoverix
- 바탕화면 아이콘
- 프로그램 추가/제거 등록 항목

삭제되지 않는 항목:
- 복구 파티션
- 백업 이미지
- 복구 런타임
- EFI/NVRAM 복구 부팅 구성

계속 진행하려면 예를 선택하세요.
"@

    try {
        $shell = New-Object -ComObject WScript.Shell
        $choice = $shell.Popup($message, 0, "Recoverix 제거 확인", 36)
        return ($choice -eq 6)
    } catch {
        Write-UninstallLog "WARNING: Uninstall confirmation popup failed: $($_.Exception.Message)"
        return $false
    }
}

try {
    Assert-Administrator
    $resolvedInstallRoot = Resolve-RecoverixInstallRoot -Root $InstallRoot
    Write-UninstallLog "Recoverix Windows component uninstall started. InstallRoot=$resolvedInstallRoot KeepData=$KeepData"

    if (-not (Confirm-RecoverixUninstall)) {
        Write-UninstallLog "Recoverix Windows component uninstall canceled by user."
        Write-Host "Recoverix uninstall canceled."
        exit 0
    }

    Stop-RecoverixProcesses

    foreach ($desktop in @(
        [Environment]::GetFolderPath("CommonDesktopDirectory"),
        [Environment]::GetFolderPath("DesktopDirectory")
    )) {
        if (-not [string]::IsNullOrWhiteSpace($desktop)) {
            foreach ($name in $ShortcutNames) {
                $shortcut = Join-Path $desktop $name
                if (Test-Path -LiteralPath $shortcut) {
                    try {
                        Remove-Item -LiteralPath $shortcut -Force -ErrorAction Stop
                        Write-UninstallLog "Removed shortcut: $shortcut"
                    } catch {
                        Write-UninstallLog "WARNING: Could not remove shortcut $shortcut : $($_.Exception.Message)"
                    }
                }
            }
        }
    }

    Remove-RecoverixScheduledTask

    Reset-RecoverixDirectoryAccess -Path $resolvedInstallRoot
    $programFilesRemoved = Remove-DirectoryWithRetry -Path $resolvedInstallRoot

    if (-not $KeepData) {
        $dataRoot = Join-Path $env:ProgramData "Recoverix"
        Reset-RecoverixDirectoryAccess -Path $dataRoot
        $dataRemoved = Remove-DirectoryWithRetry -Path $dataRoot
    }

    Remove-RecoverixRegistryEntries
    if (-not $programFilesRemoved) {
        Write-UninstallLog "WARNING: Program files were not fully removed, but uninstall registry entries were removed."
    }
    if ((-not $KeepData) -and (-not $dataRemoved)) {
        Write-UninstallLog "WARNING: ProgramData was not fully removed, but uninstall registry entries were removed."
    }
    Write-UninstallLog "Recoverix Windows component uninstall completed. Recovery partitions, backup images, EFI files, and NVRAM entries were not removed."

    Write-Host "Recoverix Windows components removed."
    Write-Host "EFI entries, NVRAM entries, and recovery partitions are not removed by this script."
    exit 0
} catch {
    Write-UninstallLog ("ERROR: " + $_.Exception.Message)
    Write-Error $_.Exception.Message
    exit 1
}

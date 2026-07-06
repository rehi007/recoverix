param(
    [string]$ProductVersion = "T.1.0.0",
    [switch]$SkipInitialConfirm
)

$ErrorActionPreference = 'Stop'
$LogPath = Join-Path $env:TEMP ("RecoverixCleanup-{0}.log" -f $ProductVersion)

function Write-CleanupLog {
    param([string]$Message)

    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Encoding UTF8 -Path $LogPath -Value $line
}

function Assert-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Recoverix cleanup must be run as Administrator."
    }
}

function Invoke-LoggedProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    Write-CleanupLog ("Running: {0} {1}" -f $FilePath, ($Arguments -join " "))
    $output = & $FilePath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) {
        Write-CleanupLog ("Output: {0}" -f (($output | ForEach-Object { [string]$_ }) -join " "))
    }
    Write-CleanupLog ("Exit code: {0}" -f $exitCode)
    if (($exitCode -ne 0) -and (-not $AllowFailure)) {
        throw ("Command failed: {0} {1}; rc={2}" -f $FilePath, ($Arguments -join " "), $exitCode)
    }
    return $exitCode
}

function Confirm-Cleanup {
    if ($SkipInitialConfirm) {
        return $true
    }

    $message = @"
Recoverix cleanup will remove:

- Recoverix Windows Agent and scheduled task
- Recoverix boot entries from firmware/NVRAM
- Recoverix EFI boot files
- Recoverix Windows files, desktop shortcut, and uninstall entry

It will NOT delete recovery partitions or backup images.
Delete RECOVERY_LINUX and RECOVERY_IMAGE manually from Disk Management.

IMPORTANT:
Windows must be restarted after cleanup.
Do not reinstall Recoverix or delete/merge recovery partitions before restarting.

Continue?
"@

    try {
        $shell = New-Object -ComObject WScript.Shell
        $choice = $shell.Popup($message, 0, "Recoverix Cleanup", 52)
        return ($choice -eq 6)
    } catch {
        Write-CleanupLog "Confirmation popup failed: $($_.Exception.Message)"
        return $false
    }
}

function Stop-RecoverixProcesses {
    foreach ($name in @("RecoverixStatus", "recoverix-nvram-writer")) {
        $processes = Get-Process -Name $name -ErrorAction SilentlyContinue
        foreach ($process in $processes) {
            try {
                Write-CleanupLog "Stopping process: $($process.ProcessName) pid=$($process.Id)"
                Stop-Process -Id $process.Id -Force -ErrorAction Stop
            } catch {
                Write-CleanupLog "WARNING: Could not stop process $name pid=$($process.Id): $($_.Exception.Message)"
            }
        }
    }
}

function Remove-RecoverixScheduledTask {
    try {
        Invoke-LoggedProcess -FilePath "schtasks.exe" -Arguments @("/Delete", "/TN", "RecoveryBootMonitor", "/F") -AllowFailure | Out-Null
    } catch {
        Write-CleanupLog "WARNING: Scheduled task removal failed: $($_.Exception.Message)"
    }
}

function Get-RecoverixFirmwareIdentifiers {
    $output = & bcdedit.exe /enum firmware /v 2>&1
    Write-CleanupLog ("bcdedit firmware output: {0}" -f (($output | ForEach-Object { [string]$_ }) -join " | "))

    $blocks = New-Object System.Collections.Generic.List[object]
    $current = New-Object System.Collections.Generic.List[string]
    foreach ($line in $output) {
        $text = [string]$line
        if ([string]::IsNullOrWhiteSpace($text)) {
            if ($current.Count -gt 0) {
                $blocks.Add(@($current.ToArray()))
                $current.Clear()
            }
            continue
        }
        $current.Add($text)
    }
    if ($current.Count -gt 0) {
        $blocks.Add(@($current.ToArray()))
    }

    $ids = New-Object System.Collections.Generic.List[string]
    foreach ($block in $blocks) {
        $blockText = ($block -join "`n")
        if ($blockText -notmatch "(?i)(Recoverix|RecoveryBoot|Start Recoverix)") {
            continue
        }
        $matches = [regex]::Matches($blockText, "\{[0-9a-fA-F-]{36}\}")
        foreach ($match in $matches) {
            $id = $match.Value
            if ($id -in @("{fwbootmgr}", "{bootmgr}", "{current}", "{default}")) {
                continue
            }
            if (-not $ids.Contains($id)) {
                $ids.Add($id)
            }
        }
    }
    return @($ids.ToArray())
}

function Remove-RecoverixFirmwareEntries {
    try {
        $ids = Get-RecoverixFirmwareIdentifiers
        foreach ($id in $ids) {
            Invoke-LoggedProcess -FilePath "bcdedit.exe" -Arguments @("/delete", $id, "/f") -AllowFailure | Out-Null
        }
        Invoke-LoggedProcess -FilePath "bcdedit.exe" -Arguments @("/deletevalue", "{fwbootmgr}", "bootsequence") -AllowFailure | Out-Null
        Invoke-LoggedProcess -FilePath "bcdedit.exe" -Arguments @("/deletevalue", "{fwbootmgr}", "bootnext") -AllowFailure | Out-Null
        Invoke-LoggedProcess -FilePath "bcdedit.exe" -Arguments @("/set", "{fwbootmgr}", "displayorder", "{bootmgr}", "/addfirst") -AllowFailure | Out-Null
    } catch {
        Write-CleanupLog "WARNING: Firmware cleanup failed: $($_.Exception.Message)"
    }
}

function Get-FreeDriveLetter {
    $used = @(Get-PSDrive -PSProvider FileSystem | ForEach-Object { $_.Name.ToUpperInvariant() })
    foreach ($letter in @("Z", "Y", "X", "W", "V", "U", "T", "S", "R")) {
        if ($letter -notin $used) {
            return $letter
        }
    }
    throw "No free drive letter was available to mount the EFI System Partition."
}

function Get-FileSha256OrEmpty {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    try {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    } catch {
        Write-CleanupLog "WARNING: Could not hash $Path : $($_.Exception.Message)"
        return ""
    }
}

function Remove-DirectoryIfPresent {
    param([string]$Path)

    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        Write-CleanupLog "Removed directory: $Path"
    } else {
        Write-CleanupLog "Directory already absent: $Path"
    }
}

function Repair-WindowsFallbackBoot {
    param([string]$EspRoot)

    $efi = Join-Path $EspRoot "EFI"
    $bootDir = Join-Path $efi "Boot"
    $bootx64 = Join-Path $bootDir "bootx64.efi"
    $bootGrub = Join-Path $bootDir "grubx64.efi"
    $windowsBoot = Join-Path $efi "Microsoft\Boot\bootmgfw.efi"
    $candidateFiles = @(
        (Join-Path $efi "RecoveryBoot\shimx64.efi"),
        (Join-Path $efi "RecoveryBoot\grubx64.efi"),
        (Join-Path $efi "RecoverixDirect\shimx64.efi"),
        (Join-Path $efi "RecoverixDirect\grubx64.efi")
    )

    $candidateHashes = @{}
    foreach ($file in $candidateFiles) {
        $hash = Get-FileSha256OrEmpty -Path $file
        if (-not [string]::IsNullOrWhiteSpace($hash)) {
            $candidateHashes[$hash] = $true
        }
    }

    $bootx64Hash = Get-FileSha256OrEmpty -Path $bootx64
    $bootGrubHash = Get-FileSha256OrEmpty -Path $bootGrub
    $fallbackLooksRecoverix = (
        (-not [string]::IsNullOrWhiteSpace($bootx64Hash) -and $candidateHashes.ContainsKey($bootx64Hash)) -or
        (-not [string]::IsNullOrWhiteSpace($bootGrubHash) -and $candidateHashes.ContainsKey($bootGrubHash))
    )

    if ($fallbackLooksRecoverix -and (Test-Path -LiteralPath $bootGrub)) {
        Remove-Item -LiteralPath $bootGrub -Force
        Write-CleanupLog "Removed Recoverix fallback GRUB file: $bootGrub"
    }

    if ((Test-Path -LiteralPath $windowsBoot) -and ($fallbackLooksRecoverix -or -not (Test-Path -LiteralPath $bootx64))) {
        New-Item -ItemType Directory -Force -Path $bootDir | Out-Null
        Copy-Item -LiteralPath $windowsBoot -Destination $bootx64 -Force
        Write-CleanupLog "Restored Windows fallback bootloader: $bootx64"
    } else {
        Write-CleanupLog "Windows fallback restore skipped. fallbackLooksRecoverix=$fallbackLooksRecoverix windowsBootExists=$(Test-Path -LiteralPath $windowsBoot)"
    }
}

function Remove-RecoverixEfiFiles {
    $letter = Get-FreeDriveLetter
    $drive = "${letter}:"
    $espRoot = "${drive}\"
    $mounted = $false
    try {
        Invoke-LoggedProcess -FilePath "mountvol.exe" -Arguments @($drive, "/S") | Out-Null
        $mounted = $true
        Write-CleanupLog "Mounted EFI System Partition at $drive"

        Repair-WindowsFallbackBoot -EspRoot $espRoot
        Remove-DirectoryIfPresent -Path (Join-Path $espRoot "EFI\RecoveryBoot")
        Remove-DirectoryIfPresent -Path (Join-Path $espRoot "EFI\RecoverixDirect")
        Remove-DirectoryIfPresent -Path (Join-Path $espRoot "EFI\Recoverix")
    } catch {
        Write-CleanupLog "WARNING: EFI cleanup failed: $($_.Exception.Message)"
    } finally {
        if ($mounted) {
            try {
                Invoke-LoggedProcess -FilePath "mountvol.exe" -Arguments @($drive, "/D") -AllowFailure | Out-Null
            } catch {
                Write-CleanupLog "WARNING: EFI unmount failed: $($_.Exception.Message)"
            }
        }
    }
}

function Reset-DirectoryAccess {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    Invoke-LoggedProcess -FilePath "takeown.exe" -Arguments @("/F", $Path, "/R", "/D", "Y") -AllowFailure | Out-Null
    Invoke-LoggedProcess -FilePath "icacls.exe" -Arguments @($Path, "/inheritance:e", "/T", "/C") -AllowFailure | Out-Null
    Invoke-LoggedProcess -FilePath "icacls.exe" -Arguments @($Path, "/grant", "*S-1-5-18:(OI)(CI)(F)", "*S-1-5-32-544:(OI)(CI)(F)", "/T", "/C") -AllowFailure | Out-Null
}

function Remove-DirectoryWithRetry {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        Write-CleanupLog "Directory already absent: $Path"
        return
    }

    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        } catch {
            Write-CleanupLog ("Remove attempt {0} failed for {1}: {2}" -f $attempt, $Path, $_.Exception.Message)
        }
        if (-not (Test-Path -LiteralPath $Path)) {
            Write-CleanupLog "Removed directory: $Path"
            return
        }
        Start-Sleep -Seconds 1
    }
    Write-CleanupLog "WARNING: Directory still exists after retries: $Path"
}

function Remove-RecoverixShortcuts {
    $shortcutPatterns = @("Recoverix*.lnk")
    foreach ($desktop in @(
        [Environment]::GetFolderPath("CommonDesktopDirectory"),
        [Environment]::GetFolderPath("DesktopDirectory")
    )) {
        if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path -LiteralPath $desktop)) {
            continue
        }
        foreach ($pattern in $shortcutPatterns) {
            Get-ChildItem -LiteralPath $desktop -Filter $pattern -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
                    Write-CleanupLog "Removed shortcut: $($_.FullName)"
                } catch {
                    Write-CleanupLog "WARNING: Could not remove shortcut $($_.FullName): $($_.Exception.Message)"
                }
            }
        }
    }
}

function Remove-RecoverixRegistryEntries {
    $paths = @(
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix Status",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus",
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix",
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus"
    )
    foreach ($path in $paths) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
            Write-CleanupLog "Removed registry entry: $path"
        }
    }
}

function Remove-RecoverixWindowsFiles {
    $installRoot = Join-Path $env:ProgramFiles "Recoverix"
    $dataRoot = Join-Path $env:ProgramData "Recoverix"

    Reset-DirectoryAccess -Path $installRoot
    Reset-DirectoryAccess -Path $dataRoot
    Remove-DirectoryWithRetry -Path $installRoot
    Remove-DirectoryWithRetry -Path $dataRoot
}

function Write-PartitionReminder {
    try {
        $partitions = Get-Partition -ErrorAction Stop | Where-Object {
            $_.GptType -or $_.DriveLetter -or $_.Size
        }
        foreach ($partition in $partitions) {
            $volume = $null
            try {
                $volume = $partition | Get-Volume -ErrorAction SilentlyContinue
            } catch {
                $volume = $null
            }
            if ($null -ne $volume -and $volume.FileSystemLabel -match "(?i)^RECOVERY_(LINUX|IMAGE)$") {
                Write-CleanupLog ("Manual partition cleanup still required: Disk={0} Partition={1} Label={2} Size={3}" -f $partition.DiskNumber, $partition.PartitionNumber, $volume.FileSystemLabel, $partition.Size)
            }
        }
    } catch {
        Write-CleanupLog "Partition reminder scan skipped: $($_.Exception.Message)"
    }
}

try {
    if (Test-Path -LiteralPath $LogPath) {
        Remove-Item -LiteralPath $LogPath -Force
    }
    Write-CleanupLog "Recoverix cleanup started."
    Assert-Administrator
    if (-not (Confirm-Cleanup)) {
        Write-CleanupLog "Recoverix cleanup canceled by user."
        exit 1223
    }

    Stop-RecoverixProcesses
    Remove-RecoverixScheduledTask
    Remove-RecoverixFirmwareEntries
    Remove-RecoverixEfiFiles
    Remove-RecoverixShortcuts
    Remove-RecoverixRegistryEntries
    Remove-RecoverixWindowsFiles
    Write-PartitionReminder

    Write-CleanupLog "Recoverix cleanup completed. Recovery partitions and backup images were not deleted."
    Write-Host "Recoverix cleanup completed. Log: $LogPath"
    exit 0
} catch {
    Write-CleanupLog ("ERROR: " + $_.Exception.Message)
    Write-Error $_.Exception.Message
    exit 1
}

$ErrorActionPreference = 'Stop'

param(
    [string]$PackageRoot = "",
    [switch]$PreflightOnly,
    [switch]$RegisterBoot,
    [switch]$SkipNvram,
    [switch]$SkipTask,
    [switch]$SkipStatusShortcut
)

$ProductName = "Recoverix"
$ProductVersion = "T.1.0.0"
$Publisher = "FORYOUCOM"
$SupportPhone = "1544-1879"
$SupportEmail = "help@foryoucom.co.kr"

function Write-Step($Message) {
    Write-Host "[Recoverix] $Message"
}

function Assert-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Recoverix installer must be run as Administrator."
    }
}

function Resolve-PackageRoot {
    param([string]$Root)
    if ([string]::IsNullOrWhiteSpace($Root)) {
        $Root = Join-Path $PSScriptRoot "..\.."
    }
    return (Resolve-Path $Root).Path
}

function Copy-DirectoryContents {
    param(
        [string]$Source,
        [string]$Destination
    )
    if (-not (Test-Path $Source)) {
        throw "Required installer payload path was not found: $Source"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

function Get-RecoverixPartitionPlan {
    $volume = Get-Volume -DriveLetter C
    $partition = Get-Partition -DriveLetter C
    $disk = Get-Disk -Number $partition.DiskNumber

    $usedBytes = [int64]($volume.Size - $volume.SizeRemaining)
    $gib = [int64](1024 * 1024 * 1024)
    $recoveryLinuxBytes = 4 * $gib
    $reservedBytes = 100 * 1024 * 1024
    $calculatedImageBytes = [int64]([math]::Ceiling($usedBytes * 0.75) + (5 * $gib))
    $minimumImageBytes = 45 * $gib
    if ($calculatedImageBytes -lt $minimumImageBytes) {
        $calculatedImageBytes = $minimumImageBytes
    }

    [PSCustomObject]@{
        ComputerName = $env:COMPUTERNAME
        UserName = $env:USERNAME
        Product = $ProductName
        Version = $ProductVersion
        Publisher = $Publisher
        SupportPhone = $SupportPhone
        SupportEmail = $SupportEmail
        WindowsDrive = "C:"
        DiskNumber = $partition.DiskNumber
        DiskFriendlyName = $disk.FriendlyName
        DiskPartitionStyle = $disk.PartitionStyle
        WindowsPartitionNumber = $partition.PartitionNumber
        WindowsPartitionSizeBytes = [int64]$partition.Size
        WindowsUsedBytes = $usedBytes
        WindowsFreeBytes = [int64]$volume.SizeRemaining
        ReservedUnallocatedBytes = $reservedBytes
        RecoveryLinuxBytes = $recoveryLinuxBytes
        RecoveryImageBytes = $calculatedImageBytes
        RecoveryImagePolicy = "max(Windows used space * 0.75 + 5GB, 45GB)"
    }
}

function Test-RecoverixPreflight {
    Assert-Administrator

    $is64 = [Environment]::Is64BitOperatingSystem
    if (-not $is64) {
        throw "Recoverix requires 64-bit Windows."
    }

    $os = Get-CimInstance Win32_OperatingSystem
    $partition = Get-Partition -DriveLetter C
    $disk = Get-Disk -Number $partition.DiskNumber
    if ($disk.PartitionStyle -ne "GPT") {
        throw "Recoverix requires a GPT system disk."
    }

    $firmwareOk = $false
    try {
        bcdedit /enum firmware | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $firmwareOk = $true
        }
    } catch {
        $firmwareOk = $false
    }
    if (-not $firmwareOk) {
        throw "Recoverix requires UEFI firmware boot entries to be accessible."
    }

    $bitlocker = Get-Command manage-bde.exe -ErrorAction SilentlyContinue
    $bitlockerStatus = "unknown"
    if ($bitlocker) {
        $bitlockerOutput = & manage-bde.exe -status C: 2>$null
        $bitlockerStatus = ($bitlockerOutput -join "`n")
        if ($bitlockerStatus -match "Protection Status:\s+Protection On") {
            throw "BitLocker protection is enabled on C:. Disable or suspend BitLocker before installing Recoverix."
        }
    }

    $plan = Get-RecoverixPartitionPlan
    $plan | Add-Member -NotePropertyName WindowsCaption -NotePropertyValue $os.Caption
    $plan | Add-Member -NotePropertyName WindowsVersion -NotePropertyValue $os.Version
    $plan | Add-Member -NotePropertyName BitLockerStatus -NotePropertyValue $bitlockerStatus
    return $plan
}

function Install-RecoverixWindowsPayload {
    param([string]$Root)

    $payload = Join-Path $Root "payload"
    $programFilesPayload = Join-Path $payload "program_files\Recoverix"
    $installRoot = Join-Path $env:ProgramFiles "Recoverix"
    $dataRoot = Join-Path $env:ProgramData "Recoverix"

    Write-Step "Installing Windows payload to $installRoot"
    Copy-DirectoryContents -Source $programFilesPayload -Destination $installRoot

    foreach ($dir in @("logs", "state", "config")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot $dir) | Out-Null
    }

    $plan = Test-RecoverixPreflight
    $planPath = Join-Path $dataRoot "state\install-preflight.json"
    $plan | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path $planPath
    Write-Step "Wrote preflight state: $planPath"

    $harden = Join-Path $installRoot "windows_agent\harden_permissions.cmd"
    if (Test-Path $harden) {
        Write-Step "Applying Recoverix file permissions"
        & $harden
        if ($LASTEXITCODE -ne 0) {
            throw "Permission hardening failed with rc=$LASTEXITCODE"
        }
    }

    if ($RegisterBoot -and -not $SkipTask) {
        $taskInstaller = Join-Path $installRoot "windows_agent\install_recoveryboot_monitor.ps1"
        if (Test-Path $taskInstaller) {
            Write-Step "Registering RecoveryBootMonitor scheduled task"
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $taskInstaller
            if ($LASTEXITCODE -ne 0) {
                throw "RecoveryBootMonitor registration failed with rc=$LASTEXITCODE"
            }
        }
    }

    if ($RegisterBoot -and -not $SkipNvram) {
        $writer = Join-Path $installRoot "native\nvram_writer\recoverix-nvram-writer.exe"
        if (Test-Path $writer) {
            Write-Step "Running Recoverix NVRAM repair"
            & $writer --skip-filesystem-extend
            if ($LASTEXITCODE -ne 0) {
                throw "Recoverix NVRAM repair failed with rc=$LASTEXITCODE"
            }
        }
    }

    if (-not $RegisterBoot) {
        Write-Step "Boot registration skipped. Enable it only after runtime partition and EFI installation are complete."
    }

    if (-not $SkipStatusShortcut) {
        $shortcut = Join-Path $installRoot "status\create_desktop_shortcut.ps1"
        if (Test-Path $shortcut) {
            Write-Step "Creating Recoverix desktop shortcut"
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $shortcut
            if ($LASTEXITCODE -ne 0) {
                throw "Desktop shortcut creation failed with rc=$LASTEXITCODE"
            }
        }
    }

    Write-Step "Windows payload installation complete"
}

try {
    $root = Resolve-PackageRoot -Root $PackageRoot
    Write-Step "$ProductName $ProductVersion installer"
    Write-Step "Package root: $root"

    $plan = Test-RecoverixPreflight
    Write-Step "Preflight passed"
    Write-Step ("Windows used space: {0:N2} GB" -f ($plan.WindowsUsedBytes / 1GB))
    Write-Step ("RECOVERY_LINUX target: {0:N2} GB" -f ($plan.RecoveryLinuxBytes / 1GB))
    Write-Step ("RECOVERY_IMAGE target: {0:N2} GB" -f ($plan.RecoveryImageBytes / 1GB))

    if ($PreflightOnly) {
        $plan | ConvertTo-Json -Depth 5
        exit 0
    }

    Install-RecoverixWindowsPayload -Root $root
    exit 0
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

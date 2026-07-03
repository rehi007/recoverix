param(
    [string]$PackageRoot = "",
    [switch]$PreflightOnly,
    [switch]$RegisterBoot,
    [switch]$SkipNvram,
    [switch]$SkipTask,
    [switch]$SkipStatusShortcut,
    [switch]$SkipRecoveryProvisioning,
    [ValidateSet("setup", "")]
    [string]$PackageType = "",
    [string]$FromVersion = "",
    [ValidateSet("Auto", "Install", "Repair", "Update")]
    [string]$ExistingInstallAction = "Auto"
)

$ErrorActionPreference = 'Stop'

$ProductName = "Recoverix"
$ProductVersion = "T.1.0.0"
$Publisher = "FORYOUCOM"
$SupportPhone = "1544-1879"
$SupportEmail = "help@foryoucom.co.kr"
$WindowsComponentsVersion = $ProductVersion
$RecoveryRuntimeVersion = $ProductVersion
$StatusAppVersion = $ProductVersion
$NvramWriterVersion = $ProductVersion
$EffectivePackageType = "setup"
$EffectiveFromVersion = ""
$UninstallRegistryPath = "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix"
$RecoveryLinuxGptType = "{0FC63DAF-8483-4772-8E79-3D69D8477DE4}"
$MicrosoftBasicDataGptType = "{EBD0A0A2-B9E5-4433-87C0-68B6B72699C7}"
$EfiSystemGptType = "{C12A7328-F81F-11D2-BA4B-00A0C93EC93B}"
$RecoveryTailReserveBytes = 100 * 1024 * 1024
$ShortcutNames = @(
    ("Recoverix " + [string][char]0xC0C1 + [string][char]0xD0DC + " " + [string][char]0xD655 + [string][char]0xC778 + ".lnk"),
    "Recoverix Status.lnk",
    "RecoverixStatus.lnk"
)
$LegacyUninstallRegistryPaths = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix Status",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Recoverix",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\RecoverixStatus"
)

function Write-Step($Message) {
    Write-Host "[Recoverix] $Message"
}

function Write-InstallLog($Message) {
    $logVersion = $script:ProductVersion
    if ([string]::IsNullOrWhiteSpace($logVersion)) {
        $logVersion = "unknown"
    }
    $logPath = Join-Path $env:TEMP ("RecoverixInstall-{0}.log" -f $logVersion)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Encoding UTF8 -Path $logPath -Value $line
}

function Get-WindowsPowerShellPath {
    $sysNative = Join-Path $env:WINDIR "SysNative\WindowsPowerShell\v1.0\powershell.exe"
    $system32 = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (Test-Path $sysNative) {
        return $sysNative
    }
    if (Test-Path $system32) {
        return $system32
    }
    return "powershell.exe"
}

function Assert-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Recoverix installer must be run as Administrator."
    }
}

function Resolve-PackageRoot {
    param([string]$Root)

    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($Root)) {
        $cleanRoot = $Root.Trim().Trim('"')
        if (-not [string]::IsNullOrWhiteSpace($cleanRoot)) {
            $candidates.Add($cleanRoot)
        }
    }

    $candidates.Add($PSScriptRoot)
    $candidates.Add((Join-Path $PSScriptRoot "..\.."))

    foreach ($candidate in $candidates) {
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        try {
            $resolved = (Resolve-Path -LiteralPath $expanded -ErrorAction Stop).Path
        } catch {
            continue
        }

        $payloadRoot = Join-Path $resolved "payload\program_files\Recoverix"
        if (Test-Path -LiteralPath $payloadRoot) {
            return $resolved
        }
    }

    throw "Required installer payload path was not found near: $PSScriptRoot"
}

function Import-RecoverixPackageMetadata {
    param([string]$Root)

    $metadataPaths = @(
        (Join-Path $Root "manifests\build-info.json"),
        (Join-Path $Root "payload\program_files\Recoverix\config\product_manifest.json")
    )

    foreach ($metadataPath in $metadataPaths) {
        if (-not (Test-Path -LiteralPath $metadataPath)) {
            continue
        }

        try {
            $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
            foreach ($pair in @(
                @("product", "ProductName"),
                @("product_name", "ProductName"),
                @("version", "ProductVersion"),
                @("publisher", "Publisher"),
                @("support_phone", "SupportPhone"),
                @("support_email", "SupportEmail"),
                @("package_type", "EffectivePackageType"),
                @("from_version", "EffectiveFromVersion")
            )) {
                $source = $pair[0]
                $target = $pair[1]
                if ($metadata.PSObject.Properties.Name -contains $source) {
                    $value = [string]$metadata.$source
                    if (-not [string]::IsNullOrWhiteSpace($value)) {
                        Set-Variable -Scope Script -Name $target -Value $value
                    }
                }
            }

            if ($metadata.PSObject.Properties.Name -contains "component_versions") {
                $components = $metadata.component_versions
                if ($components.PSObject.Properties.Name -contains "windows_components") {
                    $script:WindowsComponentsVersion = [string]$components.windows_components
                }
                if ($components.PSObject.Properties.Name -contains "recovery_runtime") {
                    $script:RecoveryRuntimeVersion = [string]$components.recovery_runtime
                }
                if ($components.PSObject.Properties.Name -contains "status_app") {
                    $script:StatusAppVersion = [string]$components.status_app
                }
                if ($components.PSObject.Properties.Name -contains "nvram_writer") {
                    $script:NvramWriterVersion = [string]$components.nvram_writer
                }
            }

            Write-InstallLog "Loaded Recoverix package metadata: $metadataPath"
            break
        } catch {
            Write-InstallLog "WARNING: Could not load package metadata from $metadataPath : $($_.Exception.Message)"
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($PackageType)) {
        $script:EffectivePackageType = $PackageType
    }
    if (-not [string]::IsNullOrWhiteSpace($FromVersion)) {
        $script:EffectiveFromVersion = $FromVersion
    }

    if ($script:EffectivePackageType -ne "setup") {
        throw "Unsupported Recoverix package type: $script:EffectivePackageType"
    }

    Write-InstallLog ("Package metadata effective values: type={0}; version={1}; from_version={2}" -f $script:EffectivePackageType, $script:ProductVersion, $script:EffectiveFromVersion)
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

function Reset-RecoverixDirectoryAccess {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    Write-InstallLog "Resetting Recoverix directory permissions: $Path"
    & takeown.exe /F $Path /R /D Y | Out-Null
    Write-InstallLog "takeown rc=$LASTEXITCODE for $Path"
    & icacls.exe $Path /inheritance:e /T /C | Out-Null
    Write-InstallLog "icacls inheritance rc=$LASTEXITCODE for $Path"
    & icacls.exe $Path /grant "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" /T /C | Out-Null
    Write-InstallLog "icacls grant rc=$LASTEXITCODE for $Path"
}

function Stop-RecoverixProcesses {
    foreach ($name in @("RecoverixStatus")) {
        $processes = Get-Process -Name $name -ErrorAction SilentlyContinue
        foreach ($process in $processes) {
            try {
                Write-InstallLog "Stopping process: $($process.ProcessName) pid=$($process.Id)"
                Stop-Process -Id $process.Id -Force -ErrorAction Stop
            } catch {
                Write-InstallLog "WARNING: Could not stop process $name pid=$($process.Id): $($_.Exception.Message)"
            }
        }
    }
}

function Remove-RecoverixDesktopShortcuts {
    foreach ($desktop in @(
        [Environment]::GetFolderPath("CommonDesktopDirectory"),
        [Environment]::GetFolderPath("DesktopDirectory")
    )) {
        if ([string]::IsNullOrWhiteSpace($desktop)) {
            continue
        }
        foreach ($name in $ShortcutNames) {
            $shortcut = Join-Path $desktop $name
            if (Test-Path -LiteralPath $shortcut) {
                try {
                    Remove-Item -LiteralPath $shortcut -Force -ErrorAction Stop
                    Write-InstallLog "Removed existing shortcut before recreate: $shortcut"
                } catch {
                    Write-InstallLog "WARNING: Could not remove existing shortcut $shortcut : $($_.Exception.Message)"
                }
            }
        }
    }
}

function Clear-RecoverixProgramFiles {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    Write-InstallLog "Clearing existing Recoverix program files: $Path"
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop |
                Remove-Item -Recurse -Force -ErrorAction Stop
            Write-InstallLog "Cleared existing Recoverix program files."
            return
        } catch {
            Write-InstallLog ("Clear attempt {0} failed for {1}: {2}" -f $attempt, $Path, $_.Exception.Message)
            Start-Sleep -Seconds 1
        }
    }

    throw "Existing Recoverix program files could not be replaced. Close Recoverix windows and run the installer again."
}

function Backup-RecoverixProgramFiles {
    param(
        [string]$InstallRoot,
        [string]$DataRoot
    )

    if (-not (Test-Path -LiteralPath $InstallRoot)) {
        return ""
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupRoot = Join-Path $DataRoot "state\rollback"
    $backupPath = Join-Path $backupRoot ("windows-components-" + $stamp)
    New-Item -ItemType Directory -Force -Path $backupPath | Out-Null
    Copy-Item -Path (Join-Path $InstallRoot "*") -Destination $backupPath -Recurse -Force -ErrorAction Stop
    Write-InstallLog "Backed up Recoverix Windows components: $backupPath"
    return $backupPath
}

function Restore-RecoverixProgramFiles {
    param(
        [string]$BackupPath,
        [string]$InstallRoot
    )

    if ([string]::IsNullOrWhiteSpace($BackupPath) -or -not (Test-Path -LiteralPath $BackupPath)) {
        return
    }

    Write-InstallLog "Restoring Recoverix Windows components from rollback backup: $BackupPath"
    Reset-RecoverixDirectoryAccess -Path $InstallRoot
    if (Test-Path -LiteralPath $InstallRoot) {
        Clear-RecoverixProgramFiles -Path $InstallRoot
    } else {
        New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    }
    Copy-Item -Path (Join-Path $BackupPath "*") -Destination $InstallRoot -Recurse -Force -ErrorAction Stop
    Write-InstallLog "Rollback restore completed."
}

function Unblock-RecoverixInstalledFiles {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $unblock = Get-Command Unblock-File -ErrorAction SilentlyContinue
    if (-not $unblock) {
        Write-InstallLog "Unblock-File is not available on this Windows version."
        return
    }

    try {
        Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue |
            Unblock-File -ErrorAction SilentlyContinue
        Write-InstallLog "Removed downloaded-file blocking markers from installed files."
    } catch {
        Write-InstallLog "WARNING: Could not unblock installed files: $($_.Exception.Message)"
    }
}

function Copy-RecoverixMaintenanceScripts {
    param(
        [string]$PackageRoot,
        [string]$InstallRoot
    )

    foreach ($name in @("uninstall_recoverix.cmd", "uninstall_recoverix.ps1")) {
        $candidatePaths = @(
            (Join-Path $PackageRoot $name),
            (Join-Path (Join-Path $PackageRoot "installer\windows") $name),
            (Join-Path $PSScriptRoot $name),
            (Join-Path (Join-Path $PSScriptRoot "installer\windows") $name)
        )
        $source = $candidatePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ([string]::IsNullOrWhiteSpace($source)) {
            throw "Required Recoverix maintenance script was not found in installer package: $name"
        }

        $destination = Join-Path $InstallRoot $name
        Copy-Item -LiteralPath $source -Destination $destination -Force
        if (-not (Test-Path -LiteralPath $destination)) {
            throw "Recoverix maintenance script was not installed: $destination"
        }
        Write-InstallLog "Copied maintenance script: $source -> $destination"
    }
}

function Get-DirectorySizeKb {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return 0
    }

    $bytes = 0
    Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { -not $_.PSIsContainer } |
        ForEach-Object { $bytes += [int64]$_.Length }
    return [int]([math]::Ceiling($bytes / 1KB))
}

function Convert-RecoverixVersionToParts {
    param([string]$Version)

    $matches = [regex]::Matches($Version, "\d+")
    if ($matches.Count -lt 3) {
        throw "Unsupported Recoverix version format: $Version"
    }

    return @(
        [int]$matches[0].Value,
        [int]$matches[1].Value,
        [int]$matches[2].Value
    )
}

function Compare-RecoverixVersion {
    param(
        [string]$Left,
        [string]$Right
    )

    $leftParts = Convert-RecoverixVersionToParts -Version $Left
    $rightParts = Convert-RecoverixVersionToParts -Version $Right
    for ($i = 0; $i -lt 3; $i++) {
        if ($leftParts[$i] -lt $rightParts[$i]) {
            return -1
        }
        if ($leftParts[$i] -gt $rightParts[$i]) {
            return 1
        }
    }
    return 0
}

function Get-RecoverixInstalledVersion {
    param(
        [string]$InstallRoot,
        [string]$DataRoot
    )

    foreach ($registryPath in @($UninstallRegistryPath) + $LegacyUninstallRegistryPaths) {
        if (Test-Path -LiteralPath $registryPath) {
            $displayVersion = (Get-ItemProperty -LiteralPath $registryPath -ErrorAction SilentlyContinue).DisplayVersion
            if (-not [string]::IsNullOrWhiteSpace($displayVersion)) {
                return $displayVersion
            }
        }
    }

    foreach ($statePath in @(
        (Join-Path $DataRoot "state\install-state.json"),
        (Join-Path $InstallRoot "config\product_manifest.json")
    )) {
        if (Test-Path -LiteralPath $statePath) {
            try {
                $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
                foreach ($property in @("product_version", "version")) {
                    if ($state.PSObject.Properties.Name -contains $property) {
                        $value = $state.$property
                        if (-not [string]::IsNullOrWhiteSpace($value)) {
                            return $value
                        }
                    }
                }
            } catch {
                Write-InstallLog "WARNING: Could not read Recoverix version from $statePath : $($_.Exception.Message)"
            }
        }
    }

    return ""
}

function Get-RecoverixInstallMode {
    param(
        [string]$InstallRoot,
        [string]$DataRoot
    )

    $installRootExists = Test-Path -LiteralPath $InstallRoot
    $installedVersion = Get-RecoverixInstalledVersion -InstallRoot $InstallRoot -DataRoot $DataRoot
    if (-not $installRootExists -and [string]::IsNullOrWhiteSpace($installedVersion)) {
        return [PSCustomObject]@{
            Mode = "FreshInstall"
            InstalledVersion = ""
            InstallRootExists = $false
        }
    }

    if ([string]::IsNullOrWhiteSpace($installedVersion)) {
        return [PSCustomObject]@{
            Mode = "RepairInstall"
            InstalledVersion = "unknown"
            InstallRootExists = $installRootExists
        }
    }

    $comparison = Compare-RecoverixVersion -Left $installedVersion -Right $ProductVersion
    if ($comparison -eq 0) {
        $mode = "RepairInstall"
    } elseif ($comparison -lt 0) {
        $mode = "Update"
    } else {
        $mode = "NewerInstalled"
    }

    return [PSCustomObject]@{
        Mode = $mode
        InstalledVersion = $installedVersion
        InstallRootExists = $installRootExists
    }
}

function Show-RecoverixPopup {
    param(
        [string]$Message,
        [string]$Title,
        [int]$Type
    )

    try {
        $shell = New-Object -ComObject WScript.Shell
        return $shell.Popup($Message, 0, $Title, $Type)
    } catch {
        Write-InstallLog "WARNING: Popup failed: $($_.Exception.Message)"
        return 2
    }
}

function Get-RecoverixInstallAction {
    param([object]$InstallMode)

    if ($InstallMode.Mode -eq "FreshInstall") {
        return "Install"
    }

    if ($InstallMode.Mode -eq "NewerInstalled") {
        $message = @"
이미 더 높은 버전의 Recoverix가 설치되어 있습니다.

현재 설치된 버전: $($InstallMode.InstalledVersion)
실행한 설치파일 버전: $ProductVersion

낮은 버전으로 설치할 수 없습니다.
최신 버전 설치파일을 사용하세요.

기존 Recoverix 구성과 복구 데이터는 변경되지 않습니다.
"@
        [void](Show-RecoverixPopup -Message $message -Title "Recoverix 설치 차단" -Type 48)
        return "Cancel"
    }

    if ($ExistingInstallAction -ne "Auto") {
        Write-InstallLog "Using installer-provided existing install action: $ExistingInstallAction"
        return $ExistingInstallAction
    }

    if ($InstallMode.Mode -eq "Update") {
        $message = @"
이전 버전의 Recoverix Windows 구성요소가 이미 설치되어 있습니다.

현재 설치된 버전: $($InstallMode.InstalledVersion)
설치 프로그램 버전: $ProductVersion

업데이트를 진행하시겠습니까?
"@
        $choice = Show-RecoverixPopup -Message $message -Title "Recoverix 설치" -Type 36
        if ($choice -eq 6) {
            return "Update"
        }
        return "Cancel"
    }

    $message = @"
Recoverix $ProductVersion이 이미 설치되어 있습니다.

복구 설치를 진행하시겠습니까?

복구 설치를 진행하면 Windows 구성요소를 다시 설치합니다.
복구 파티션과 백업 이미지는 삭제되지 않습니다.

예: 복구 설치 진행
아니오: 설치 종료
"@
    $choice = Show-RecoverixPopup -Message $message -Title "Recoverix 설치" -Type 36
    if ($choice -eq 6) {
        return "Repair"
    }
    return "Cancel"
}

function Write-RecoverixInstallState {
    param(
        [string]$DataRoot,
        [string]$InstallRoot,
        [object]$InstallMode,
        [object]$Plan
    )

    $stateDir = Join-Path $DataRoot "state"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $statePath = Join-Path $stateDir "install-state.json"
    $state = [PSCustomObject]@{
        product = $ProductName
        product_version = $ProductVersion
        package_type = $EffectivePackageType
        from_version = $EffectiveFromVersion
        windows_components_version = $WindowsComponentsVersion
        recovery_runtime_version = $RecoveryRuntimeVersion
        status_app_version = $StatusAppVersion
        nvram_writer_version = $NvramWriterVersion
        install_mode = $InstallMode.Mode
        previous_version = $InstallMode.InstalledVersion
        installed_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        install_root = $InstallRoot
        publisher = $Publisher
        support_phone = $SupportPhone
        support_email = $SupportEmail
        preserved_by_windows_uninstall = @(
            "RECOVERY_LINUX partition",
            "RECOVERY_IMAGE partition",
            "backup images",
            "recovery runtime",
            "EFI recovery boot files",
            "NVRAM recovery boot entries"
        )
        preflight = $Plan
    }
    $state | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $statePath
    return $statePath
}

function Remove-RecoverixLegacyUninstallEntries {
    foreach ($registryPath in $LegacyUninstallRegistryPaths) {
        if (Test-Path -LiteralPath $registryPath) {
            Remove-Item -LiteralPath $registryPath -Recurse -Force
            Write-InstallLog "Removed legacy uninstall registry entry: $registryPath"
        }
    }
}

function New-RecoverixUninstallCommand {
    param([string]$InstallRoot)

    $uninstallCmd = Join-Path $InstallRoot "uninstall_recoverix.cmd"
    return ('"{0}"' -f $uninstallCmd)
}

function Register-RecoverixUninstallEntry {
    param([string]$InstallRoot)

    $uninstallCmd = Join-Path $InstallRoot "uninstall_recoverix.cmd"
    $displayIcon = Join-Path $InstallRoot "StatusApp\RecoverixStatus.exe"
    $displayName = "Recoverix Windows " + [string][char]0xAD6C + [string][char]0xC131 + [string][char]0xC694 + [string][char]0xC18C
    if (-not (Test-Path -LiteralPath $uninstallCmd)) {
        throw "Recoverix uninstaller command was not found: $uninstallCmd"
    }

    Remove-RecoverixLegacyUninstallEntries
    $uninstallString = New-RecoverixUninstallCommand -InstallRoot $InstallRoot

    New-Item -Path $UninstallRegistryPath -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "DisplayName" -Value $displayName -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "DisplayVersion" -Value $ProductVersion -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "Publisher" -Value $Publisher -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "HelpTelephone" -Value $SupportPhone -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "HelpLink" -Value "mailto:$SupportEmail" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "InstallLocation" -Value $InstallRoot -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "UninstallString" -Value $uninstallString -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "QuietUninstallString" -Value $uninstallString -PropertyType String -Force | Out-Null
    if (Test-Path -LiteralPath $displayIcon) {
        New-ItemProperty -Path $UninstallRegistryPath -Name "DisplayIcon" -Value $displayIcon -PropertyType String -Force | Out-Null
    }
    New-ItemProperty -Path $UninstallRegistryPath -Name "EstimatedSize" -Value (Get-DirectorySizeKb -Path $InstallRoot) -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "NoModify" -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $UninstallRegistryPath -Name "NoRepair" -Value 1 -PropertyType DWord -Force | Out-Null
}

function Invoke-RecoverixPackageUninstall {
    param(
        [string]$Root,
        [string]$InstallRoot,
        [string]$WindowsPowerShell
    )

    $candidatePaths = @(
        (Join-Path $Root "uninstall_recoverix.ps1"),
        (Join-Path (Join-Path $Root "installer\windows") "uninstall_recoverix.ps1"),
        (Join-Path $PSScriptRoot "uninstall_recoverix.ps1"),
        (Join-Path (Join-Path $PSScriptRoot "installer\windows") "uninstall_recoverix.ps1")
    )
    $uninstallScript = $candidatePaths | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($uninstallScript)) {
        throw "Recoverix uninstaller was not found in the installer package."
    }

    Write-Step "Removing Recoverix Windows components"
    Write-InstallLog "Running package uninstaller: $uninstallScript"
    & $WindowsPowerShell -NoProfile -ExecutionPolicy Bypass -File $uninstallScript -InstallRoot $InstallRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Recoverix uninstall failed with rc=$LASTEXITCODE"
    }
    Write-InstallLog "Recoverix Windows components were removed."
}

function Get-RecoverixPartitionPlan {
    $volume = Get-Volume -DriveLetter C
    $partition = Get-Partition -DriveLetter C
    $disk = Get-Disk -Number $partition.DiskNumber

    $usedBytes = [int64]($volume.Size - $volume.SizeRemaining)
    $gib = [int64](1024 * 1024 * 1024)
    $recoveryLinuxBytes = 4 * $gib
    $reservedBytes = 100 * 1024 * 1024
    $calculatedImageBytes = [int64]([math]::Ceiling($usedBytes * 0.75) + (10 * $gib))
    $minimumImageBytes = 35 * $gib
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
        RecoveryImagePolicy = "max(Windows used space * 0.75 + 10GB, 35GB)"
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

function Align-BytesUp {
    param(
        [int64]$Value,
        [int64]$Alignment = 1048576
    )
    if ($Value -le 0) {
        return 0
    }
    return [int64]([math]::Ceiling($Value / [double]$Alignment) * $Alignment)
}

function Align-BytesDown {
    param(
        [int64]$Value,
        [int64]$Alignment = 1048576
    )
    if ($Value -le 0) {
        return 0
    }
    return [int64]([math]::Floor($Value / [double]$Alignment) * $Alignment)
}

function Format-BytesGiB {
    param([int64]$Value)
    return ("{0:N1} GiB" -f ($Value / 1GB))
}

function Get-RecoverixRecoveryPackageInfo {
    param([string]$Root)

    $recoveryLinuxRoot = Join-Path $Root "payload\recovery_linux"
    $imagePath = Join-Path $recoveryLinuxRoot "recovery-linux.ext4.img"
    $metadataPath = Join-Path $recoveryLinuxRoot "recovery-linux.json"
    if (-not (Test-Path -LiteralPath $imagePath)) {
        throw "Recoverix recovery Linux image was not found: $imagePath"
    }
    if (-not (Test-Path -LiteralPath $metadataPath)) {
        throw "Recoverix recovery Linux metadata was not found: $metadataPath"
    }

    $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    return [PSCustomObject]@{
        ImagePath = $imagePath
        ImageSizeBytes = [int64](Get-Item -LiteralPath $imagePath).Length
        PartitionSizeBytes = [int64]$metadata.partition_size_bytes
        FileSystem = [string]$metadata.filesystem
        Label = [string]$metadata.label
        Uuid = [string]$metadata.uuid
        KernelVersion = [string]$metadata.kernel_version
    }
}

function Get-RawPartitionPath {
    param(
        [int]$DiskNumber,
        [int]$PartitionNumber
    )
    return (Get-RawPartitionPathCandidates -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber)[0]
}

function Get-RawPartitionPathCandidates {
    param(
        [int]$DiskNumber,
        [int]$PartitionNumber
    )
    return @(
        ("\\?\GLOBALROOT\Device\Harddisk{0}\Partition{1}" -f $DiskNumber, $PartitionNumber),
        ("\\.\GLOBALROOT\Device\Harddisk{0}\Partition{1}" -f $DiskNumber, $PartitionNumber),
        ("\\.\Harddisk{0}Partition{1}" -f $DiskNumber, $PartitionNumber)
    )
}

function Get-PhysicalDrivePath {
    param([int]$DiskNumber)
    return ("\\.\PhysicalDrive{0}" -f $DiskNumber)
}

function Get-Ext4LabelFromPartition {
    param(
        [int]$DiskNumber,
        [int]$PartitionNumber
    )

    $partition = Get-Partition -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber
    $buffer = New-Object byte[] 2048
    $read = 0
    $probeSource = ""

    foreach ($rawPath in (Get-RawPartitionPathCandidates -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber)) {
        $stream = $null
        try {
            $stream = [System.IO.File]::Open(
                $rawPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            $read = $stream.Read($buffer, 0, $buffer.Length)
            $probeSource = $rawPath
            break
        } catch {
            Write-InstallLog "Ext4 label probe path failed for disk=$DiskNumber partition=$PartitionNumber path=$rawPath : $($_.Exception.Message)"
        } finally {
            if ($stream) {
                $stream.Dispose()
            }
        }
    }

    if ($read -lt 1200) {
        $physicalPath = Get-PhysicalDrivePath -DiskNumber $DiskNumber
        $stream = $null
        try {
            $stream = [System.IO.File]::Open(
                $physicalPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            [void]$stream.Seek([int64]$partition.Offset, [System.IO.SeekOrigin]::Begin)
            $read = $stream.Read($buffer, 0, $buffer.Length)
            $probeSource = ("{0}+{1}" -f $physicalPath, $partition.Offset)
        } catch {
            Write-InstallLog "Ext4 label physical drive probe skipped for disk=$DiskNumber partition=$PartitionNumber : $($_.Exception.Message)"
            return ""
        } finally {
            if ($stream) {
                $stream.Dispose()
            }
        }
    }

    if ($read -lt 1200) {
        return ""
    }

    $magic = [System.BitConverter]::ToUInt16($buffer, 1024 + 56)
    if ($magic -ne 0xEF53) {
        return ""
    }

    $labelBytes = New-Object byte[] 16
    [Array]::Copy($buffer, 1024 + 120, $labelBytes, 0, 16)
    $label = ([System.Text.Encoding]::ASCII.GetString($labelBytes)).Trim([char]0).Trim()
    Write-InstallLog "Ext4 label probe succeeded for disk=$DiskNumber partition=$PartitionNumber source=$probeSource label=$label"
    return $label
}

function Get-RecoverixRecoveryPartitions {
    param(
        [int]$DiskNumber,
        [object]$PackageInfo = $null,
        [object]$Plan = $null
    )

    $linuxPartition = $null
    $blankLinuxCandidate = $null
    $linuxCandidates = @()
    $imagePartition = $null
    $partitions = @(Get-Partition -DiskNumber $DiskNumber | Sort-Object PartitionNumber)
    foreach ($partition in $partitions) {
        $gptType = ([string]$partition.GptType).ToUpperInvariant()
        $isRecoveryLinuxType = ($gptType -eq $RecoveryLinuxGptType.ToUpperInvariant())
        if ($isRecoveryLinuxType) {
            $linuxCandidates += $partition
            if ($null -eq $blankLinuxCandidate) {
                $matchesRecoverixRuntimeSize = $true
                if ($null -ne $PackageInfo) {
                    $minimumLinuxBytes = [int64]$PackageInfo.ImageSizeBytes
                    $maximumLinuxBytes = [int64]$PackageInfo.PartitionSizeBytes + (512 * 1024 * 1024)
                    $matchesRecoverixRuntimeSize = ([int64]$partition.Size -ge $minimumLinuxBytes) -and ([int64]$partition.Size -le $maximumLinuxBytes)
                }
                if ($matchesRecoverixRuntimeSize) {
                    $blankLinuxCandidate = $partition
                }
            }
        }

        if ($null -eq $linuxPartition) {
            $label = Get-Ext4LabelFromPartition -DiskNumber $DiskNumber -PartitionNumber $partition.PartitionNumber
            if ($label -eq "RECOVERY_LINUX") {
                $linuxPartition = $partition
            }
        }

        if ($null -eq $imagePartition) {
            try {
                $volume = $partition | Get-Volume -ErrorAction Stop
                if ($volume.FileSystemLabel -eq "RECOVERY_IMAGE") {
                    $imagePartition = $partition
                }
            } catch {
                # Unformatted, ext4, or hidden partitions may not have a Windows volume.
            }
        }
    }

    if ($null -eq $imagePartition -and $null -ne $Plan) {
        $linuxAnchor = $linuxPartition
        if ($null -eq $linuxAnchor) {
            $linuxAnchor = $blankLinuxCandidate
        }

        if ($null -ne $linuxAnchor) {
            $expectedImageBytes = [int64]$Plan.RecoveryImageBytes
            if ($expectedImageBytes -gt 0) {
                $oneGiB = [int64](1024 * 1024 * 1024)
                $minimumImageBytes = $expectedImageBytes - $oneGiB
                $maximumImageBytes = $expectedImageBytes + $oneGiB
                if ($minimumImageBytes -lt $oneGiB) {
                    $minimumImageBytes = $oneGiB
                }

                foreach ($partition in $partitions) {
                    $gptType = ([string]$partition.GptType).ToUpperInvariant()
                    $isBasicData = ($gptType -eq $MicrosoftBasicDataGptType.ToUpperInvariant())
                    $isAfterRecoveryLinux = ([int64]$partition.Offset -gt [int64]$linuxAnchor.Offset)
                    $matchesImageSize = ([int64]$partition.Size -ge $minimumImageBytes) -and ([int64]$partition.Size -le $maximumImageBytes)
                    if ($isBasicData -and $isAfterRecoveryLinux -and $matchesImageSize) {
                        $imagePartition = $partition
                        Write-InstallLog ("Using RECOVERY_IMAGE layout candidate: disk={0}; partition={1}; size={2}" -f $DiskNumber, $partition.PartitionNumber, $partition.Size)
                        break
                    }
                }
            }
        }
    }

    return [PSCustomObject]@{
        Linux = $linuxPartition
        BlankLinuxCandidate = $blankLinuxCandidate
        LinuxCandidates = $linuxCandidates
        Image = $imagePartition
    }
}

function Confirm-RecoverixRecoveryProvisioning {
    param(
        [object]$Plan,
        [object]$PackageInfo,
        [int64]$RecoveryImageBytes,
        [bool]$CreatesPartitions
    )

    if ($CreatesPartitions) {
        $message = @"
Recoverix 복구 파티션을 생성합니다.

진행되는 작업:
- Windows 파티션 축소
- Windows 파티션 뒤쪽에 100MB 미할당 공간 확보
- RECOVERY_LINUX 파티션 생성
- RECOVERY_IMAGE 파티션 생성
- 복구 런타임 및 EFI 부팅 파일 설치

예상 할당:
- RECOVERY_LINUX: $(Format-BytesGiB $PackageInfo.PartitionSizeBytes)
- RECOVERY_IMAGE: $(Format-BytesGiB $RecoveryImageBytes)
- 미할당 여유 공간: 100MB

Recoverix 설치에는 최소 약 40GB 이상의 확보 가능한 공간이 필요합니다.
실제 사용되는 공간은 Windows 사용량에 따라 자동 계산됩니다.
복구/백업 공간은 Windows 탐색기에서 일반 드라이브처럼 보이지 않을 수 있습니다.
설치 후 C: 드라이브 용량이 줄어든 것처럼 보일 수 있습니다.

설치 전 중요한 데이터는 반드시 별도로 백업해 두세요.
계속 진행하시겠습니까?
"@
    } else {
        $message = @"
기존 Recoverix 복구 파티션을 감지했습니다.

진행되는 작업:
- RECOVERY_IMAGE 백업 파티션은 유지
- RECOVERY_LINUX 복구 런타임 갱신
- EFI/NVRAM 복구 부팅 구성 갱신

계속 진행하시겠습니까?
"@
    }

    $choice = Show-RecoverixPopup -Message $message -Title "Recoverix 복구 구성" -Type 36
    return ($choice -eq 6)
}

function Copy-RecoverixImageToRawPartition {
    param(
        [string]$ImagePath,
        [int]$DiskNumber,
        [int]$PartitionNumber
    )

    $partition = Get-Partition -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber
    $imageSize = [int64](Get-Item -LiteralPath $ImagePath).Length
    if ($imageSize -gt [int64]$partition.Size) {
        throw "Recovery Linux image is larger than the target partition."
    }

    Write-Step "Writing Recovery Linux image to partition $PartitionNumber"
    Write-InstallLog "Writing Recovery Linux image: $ImagePath -> disk=$DiskNumber partition=$PartitionNumber ($imageSize bytes)"

    $inputStream = $null
    $outputStream = $null
    $targetDescription = ""
    try {
        foreach ($rawPath in (Get-RawPartitionPathCandidates -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber)) {
            try {
                $outputStream = [System.IO.File]::Open(
                    $rawPath,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Write,
                    [System.IO.FileShare]::ReadWrite
                )
                $targetDescription = $rawPath
                Write-InstallLog "Opened recovery Linux raw partition target: $targetDescription"
                break
            } catch {
                Write-InstallLog "Recovery Linux raw partition open failed: $rawPath : $($_.Exception.Message)"
            }
        }

        if ($null -eq $outputStream) {
            $physicalPath = Get-PhysicalDrivePath -DiskNumber $DiskNumber
            try {
                $outputStream = [System.IO.File]::Open(
                    $physicalPath,
                    [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::ReadWrite,
                    [System.IO.FileShare]::ReadWrite
                )
                [void]$outputStream.Seek([int64]$partition.Offset, [System.IO.SeekOrigin]::Begin)
                $targetDescription = ("{0}+{1}" -f $physicalPath, $partition.Offset)
                Write-InstallLog "Opened recovery Linux physical drive target: $targetDescription"
            } catch {
                throw "Could not open RECOVERY_LINUX raw write target. Last error: $($_.Exception.Message)"
            }
        }

        $inputStream = [System.IO.File]::Open(
            $ImagePath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::Read
        )
        $buffer = New-Object byte[] (4 * 1024 * 1024)
        $totalWritten = [int64]0
        while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $outputStream.Write($buffer, 0, $read)
            $totalWritten += [int64]$read
        }
        $outputStream.Flush()
        Write-InstallLog "Recovery Linux image write completed: target=$targetDescription; bytes=$totalWritten"
    } finally {
        if ($outputStream) {
            $outputStream.Dispose()
        }
        if ($inputStream) {
            $inputStream.Dispose()
        }
    }
}

function Get-FreeDriveLetter {
    $used = @{}
    Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
        $used[[string]$_.DriveLetter] = $true
    }
    foreach ($letter in @("S", "T", "U", "V", "W", "X", "Y", "Z", "R", "Q", "P")) {
        if (-not $used.ContainsKey($letter)) {
            return $letter
        }
    }
    throw "No free drive letter is available for temporary ESP mount."
}

function Get-EspVolumeSerial {
    param(
        [string]$EspRoot,
        [int]$DiskNumber = -1,
        [int]$PartitionNumber = -1
    )

    $volumeName = $EspRoot.TrimEnd("\")
    try {
        $output = & cmd.exe /c "vol $volumeName" 2>$null
        $text = ($output -join "`n")
        if ($text -match "([0-9A-Fa-f]{4})-([0-9A-Fa-f]{4})") {
            return ("{0}-{1}" -f $Matches[1], $Matches[2]).ToUpperInvariant()
        }
    } catch {
        Write-InstallLog "ESP vol command UUID read skipped for $EspRoot : $($_.Exception.Message)"
    }

    if ($DiskNumber -ge 0 -and $PartitionNumber -ge 0) {
        $partition = Get-Partition -DiskNumber $DiskNumber -PartitionNumber $PartitionNumber
        $physicalPath = Get-PhysicalDrivePath -DiskNumber $DiskNumber
        $stream = $null
        try {
            $stream = [System.IO.File]::Open(
                $physicalPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            [void]$stream.Seek([int64]$partition.Offset, [System.IO.SeekOrigin]::Begin)
            $buffer = New-Object byte[] 512
            $read = $stream.Read($buffer, 0, $buffer.Length)
            if ($read -lt 90) {
                throw "ESP boot sector read was too short."
            }

            $fat32Name = ([System.Text.Encoding]::ASCII.GetString($buffer, 82, 8)).Trim()
            if ($fat32Name -eq "FAT32") {
                $serial = [System.BitConverter]::ToUInt32($buffer, 67)
            } else {
                $serial = [System.BitConverter]::ToUInt32($buffer, 39)
            }
            return ("{0:X4}-{1:X4}" -f (($serial -shr 16) -band 0xFFFF), ($serial -band 0xFFFF))
        } catch {
            Write-InstallLog "ESP raw FAT UUID read skipped for disk=$DiskNumber partition=$PartitionNumber : $($_.Exception.Message)"
        } finally {
            if ($stream) {
                $stream.Dispose()
            }
        }
    }

    throw "Could not read ESP filesystem UUID from $EspRoot"
}

function Wait-RecoverixPathReady {
    param(
        [string]$Path,
        [int]$TimeoutSeconds = 8
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-Path -LiteralPath $Path) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Backup-RecoverixEfiTarget {
    param(
        [string]$EspRoot,
        [string]$RelativePath,
        [string]$DataRoot
    )

    try {
        $source = Join-Path $EspRoot $RelativePath
        if (-not (Test-Path -LiteralPath $source)) {
            return
        }

        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backupDir = Join-Path $DataRoot ("backup\efi\" + $stamp)
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $safeName = $RelativePath.Replace("\", "_").Replace("/", "_").Replace(":", "_")
        $backupPath = Join-Path $backupDir $safeName
        Copy-Item -LiteralPath $source -Destination $backupPath -Force
        Write-InstallLog "Backed up EFI file: $source -> $backupPath"
    } catch {
        Write-InstallLog "WARNING: EFI backup skipped for $RelativePath : $($_.Exception.Message)"
    }
}

function Remove-RecoverixEspAccessPath {
    param(
        [object]$Esp,
        [string]$EspRoot
    )

    if ([string]::IsNullOrWhiteSpace($EspRoot)) {
        return
    }

    $drive = $EspRoot.TrimEnd("\")
    if ($drive.Length -lt 2) {
        return
    }

    if ($drive -notmatch "^[A-Za-z]:$") {
        Write-InstallLog "Removing ESP folder access path: $EspRoot"
        try {
            Remove-PartitionAccessPath `
                -DiskNumber $Esp.DiskNumber `
                -PartitionNumber $Esp.PartitionNumber `
                -AccessPath $EspRoot `
                -ErrorAction Stop
        } catch {
            Write-InstallLog "Remove-PartitionAccessPath for ESP folder skipped: $($_.Exception.Message)"
        }
        try {
            & mountvol.exe $EspRoot /D | Out-Null
            Write-InstallLog "mountvol ESP folder remove rc=$LASTEXITCODE"
        } catch {
            Write-InstallLog "mountvol ESP folder remove skipped: $($_.Exception.Message)"
        }
        return
    }

    $letter = $drive.Substring(0, 1)
    $driveRoot = ("{0}:\" -f $letter)

    Write-InstallLog "Removing ESP access path by Remove-PartitionAccessPath: $EspRoot"
    try {
        Remove-PartitionAccessPath `
            -DiskNumber $Esp.DiskNumber `
            -PartitionNumber $Esp.PartitionNumber `
            -AccessPath $EspRoot `
            -ErrorAction Stop
    } catch {
        Write-InstallLog "Remove-PartitionAccessPath for ESP skipped: $($_.Exception.Message)"
    }

    Write-InstallLog "Removing ESP access path by mountvol: $driveRoot /D"
    & mountvol.exe $driveRoot /D | Out-Null
    Write-InstallLog "mountvol ESP remove rc=$LASTEXITCODE"

    $diskpartScript = Join-Path $env:TEMP ("recoverix-remove-esp-letter-{0}.txt" -f ([Guid]::NewGuid().ToString("N")))
    try {
        @(
            ("select disk {0}" -f $Esp.DiskNumber),
            ("select partition {0}" -f $Esp.PartitionNumber),
            ("remove letter={0} noerr" -f $letter),
            "exit"
        ) | Set-Content -LiteralPath $diskpartScript -Encoding ASCII
        Write-InstallLog "Removing ESP access path by diskpart letter=$letter"
        $diskpartOutput = & diskpart.exe /s $diskpartScript 2>&1
        foreach ($line in $diskpartOutput) {
            Write-InstallLog "diskpart ESP remove: $line"
        }
        Write-InstallLog "diskpart ESP remove rc=$LASTEXITCODE"
    } catch {
        Write-InstallLog "diskpart ESP remove skipped: $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $diskpartScript -Force -ErrorAction SilentlyContinue
    }

    $volumeScript = Join-Path $env:TEMP ("recoverix-remove-esp-volume-{0}.txt" -f ([Guid]::NewGuid().ToString("N")))
    try {
        @(
            ("select volume {0}" -f $letter),
            ("remove letter={0} noerr" -f $letter),
            "exit"
        ) | Set-Content -LiteralPath $volumeScript -Encoding ASCII
        Write-InstallLog "Removing ESP access path by diskpart volume=$letter"
        $volumeOutput = & diskpart.exe /s $volumeScript 2>&1
        foreach ($line in $volumeOutput) {
            Write-InstallLog "diskpart ESP volume remove: $line"
        }
        Write-InstallLog "diskpart ESP volume remove rc=$LASTEXITCODE"
    } catch {
        Write-InstallLog "diskpart ESP volume remove skipped: $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $volumeScript -Force -ErrorAction SilentlyContinue
    }
}

function Remove-RecoverixTemporaryEspDriveLetter {
    param([string]$Letter = "S")

    try {
        $volume = Get-Volume -DriveLetter $Letter -ErrorAction Stop
        $size = [int64]$volume.Size
        $fileSystem = [string]$volume.FileSystem
        $label = [string]$volume.FileSystemLabel
        $oneGiB = [int64](1024 * 1024 * 1024)
        $espLike = ($size -gt 0 -and $size -le $oneGiB -and $fileSystem -match "FAT")
        if (-not $espLike) {
            Write-InstallLog "Temporary ESP drive cleanup skipped for ${Letter}:; volume does not look like ESP. fs=$fileSystem label=$label size=$size"
            return
        }

        $root = ("{0}:\" -f $Letter)
        Write-InstallLog "Removing leftover temporary ESP drive letter: $root fs=$fileSystem label=$label size=$size"
        & mountvol.exe $root /D | Out-Null
        Write-InstallLog "leftover ESP mountvol remove rc=$LASTEXITCODE"

        $scriptPath = Join-Path $env:TEMP ("recoverix-remove-leftover-esp-{0}.txt" -f ([Guid]::NewGuid().ToString("N")))
        try {
            @(
                ("select volume {0}" -f $Letter),
                ("remove letter={0} noerr" -f $Letter),
                "exit"
            ) | Set-Content -LiteralPath $scriptPath -Encoding ASCII
            $output = & diskpart.exe /s $scriptPath 2>&1
            foreach ($line in $output) {
                Write-InstallLog "leftover ESP diskpart remove: $line"
            }
            Write-InstallLog "leftover ESP diskpart remove rc=$LASTEXITCODE"
        } finally {
            Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-InstallLog "Temporary ESP drive cleanup skipped for ${Letter}: $($_.Exception.Message)"
    }
}

function Render-RecoverixGrubConfig {
    param(
        [string]$TemplatePath,
        [string]$DestinationPath,
        [string]$RecoveryUuid,
        [string]$KernelVersion,
        [string]$EspUuid,
        [string]$BootTimeoutSec
    )

    $content = Get-Content -LiteralPath $TemplatePath -Raw
    $content = $content.Replace("@RECOVERY_UUID@", $RecoveryUuid)
    $content = $content.Replace("@KERNEL_VERSION@", $KernelVersion)
    $content = $content.Replace("@ESP_UUID@", $EspUuid)
    $content = $content.Replace("@RECOVERY_HOTKEY@", "q")
    $content = $content.Replace("@BOOT_TIMEOUT_SEC@", $BootTimeoutSec)
    Set-Content -LiteralPath $DestinationPath -Value $content -Encoding ASCII
}

function Render-RecoverixGrubBootstrapConfig {
    param(
        [string]$DestinationPath,
        [string]$EspUuid
    )

$content = @'
# Recoverix GRUB bootstrap.
# Some signed GRUB builds load grub.cfg from their embedded vendor prefix.
# Redirect that prefix to the Recoverix configuration installed on the ESP.

insmod part_gpt
insmod fat
insmod search
insmod search_fs_uuid

search --no-floppy --fs-uuid --set=recoverix_esp @ESP_UUID@

if [ -f ($recoverix_esp)/EFI/RecoveryBoot/grub.cfg ]; then
    configfile ($recoverix_esp)/EFI/RecoveryBoot/grub.cfg
fi

if [ -f ($recoverix_esp)/EFI/RecoverixDirect/grub.cfg ]; then
    configfile ($recoverix_esp)/EFI/RecoverixDirect/grub.cfg
fi

echo "Recoverix GRUB configuration was not found."
sleep 3
'@
    $content = $content.Replace("@ESP_UUID@", $EspUuid)
    $parent = Split-Path -Parent $DestinationPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Set-Content -LiteralPath $DestinationPath -Value $content -Encoding ASCII
}

function Copy-RecoverixEfiDirectory {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$DataRoot
    )

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($name in @("shimx64.efi", "grubx64.efi")) {
        $sourceFile = Join-Path $Source $name
        $destFile = Join-Path $Destination $name
        if (-not (Test-Path -LiteralPath $sourceFile)) {
            throw "Required EFI file missing: $sourceFile"
        }
        Backup-RecoverixEfiTarget -EspRoot (Split-Path -Parent (Split-Path -Parent $Destination)) -RelativePath ((Split-Path -Leaf $Destination) + "\" + $name) -DataRoot $DataRoot
        Copy-Item -LiteralPath $sourceFile -Destination $destFile -Force
    }
}

function Install-RecoverixEfiFiles {
    param(
        [string]$Root,
        [object]$Plan,
        [object]$PackageInfo,
        [string]$DataRoot
    )

    Write-Step "Installing Recoverix EFI files"
    Write-InstallLog "Installing Recoverix EFI files."
    $efiPayload = Join-Path $Root "payload\efi"
    $esp = Get-Partition -DiskNumber $Plan.DiskNumber |
        Where-Object { ([string]$_.GptType).ToUpperInvariant() -eq $EfiSystemGptType.ToUpperInvariant() } |
        Select-Object -First 1
    if ($null -eq $esp) {
        throw "EFI System Partition was not found on disk $($Plan.DiskNumber)."
    }
    Write-InstallLog ("Found ESP: disk={0}; partition={1}; size={2}; drive_letter={3}" -f $esp.DiskNumber, $esp.PartitionNumber, $esp.Size, $esp.DriveLetter)

    $addedAccessPath = $false
    $accessPathMethod = ""
    $espRoot = Join-Path $DataRoot ("mount\esp-" + ([Guid]::NewGuid().ToString("N")))
    if (-not $espRoot.EndsWith("\")) {
        $espRoot = $espRoot + "\"
    }

    Remove-RecoverixTemporaryEspDriveLetter -Letter "S"
    if ($esp.DriveLetter) {
        Remove-RecoverixEspAccessPath -Esp $esp -EspRoot ("{0}:\" -f $esp.DriveLetter)
        $esp = Get-Partition -DiskNumber $Plan.DiskNumber -PartitionNumber $esp.PartitionNumber
    }

    New-Item -ItemType Directory -Force -Path $espRoot | Out-Null
    Write-InstallLog "Mounting ESP with temporary folder $espRoot by Add-PartitionAccessPath."
    try {
        Add-PartitionAccessPath -DiskNumber $esp.DiskNumber -PartitionNumber $esp.PartitionNumber -AccessPath $espRoot -ErrorAction Stop
        $addedAccessPath = $true
        $accessPathMethod = "FolderAccessPath"
        if (-not (Wait-RecoverixPathReady -Path $espRoot -TimeoutSeconds 8)) {
            throw "ESP folder path did not become accessible after Add-PartitionAccessPath: $espRoot"
        }
    } catch {
        Write-InstallLog "Add-PartitionAccessPath folder mount failed for ESP $espRoot : $($_.Exception.Message)"
        try {
            Remove-PartitionAccessPath -DiskNumber $esp.DiskNumber -PartitionNumber $esp.PartitionNumber -AccessPath $espRoot -ErrorAction SilentlyContinue
        } catch {
            Write-InstallLog "ESP folder cleanup after Add-PartitionAccessPath failure skipped: $($_.Exception.Message)"
        }
        throw
    }

    try {
        Write-InstallLog "Using ESP root $espRoot by $accessPathMethod."
        $espUuid = Get-EspVolumeSerial -EspRoot $espRoot -DiskNumber $esp.DiskNumber -PartitionNumber $esp.PartitionNumber
        Write-InstallLog "ESP filesystem UUID: $espUuid"
        $recoveryBootSource = Join-Path $efiPayload "RecoveryBoot"
        $directSource = Join-Path $efiPayload "RecoverixDirect"
        $fallbackSource = Join-Path $efiPayload "Boot"

        $recoveryBootDest = Join-Path $espRoot "EFI\RecoveryBoot"
        $directDest = Join-Path $espRoot "EFI\RecoverixDirect"
        $fallbackDest = Join-Path $espRoot "EFI\Boot"
        $ubuntuPrefixDest = Join-Path $espRoot "EFI\ubuntu"
        $bootGrubDest = Join-Path $espRoot "boot\grub"

        Write-InstallLog "Creating EFI target directories: $recoveryBootDest ; $directDest ; $fallbackDest ; $ubuntuPrefixDest ; $bootGrubDest"
        New-Item -ItemType Directory -Force -Path $recoveryBootDest, $directDest, $fallbackDest, $ubuntuPrefixDest, $bootGrubDest | Out-Null

        foreach ($entry in @(
            @{ Source = $recoveryBootSource; Destination = $recoveryBootDest },
            @{ Source = $directSource; Destination = $directDest }
        )) {
            foreach ($name in @("shimx64.efi", "grubx64.efi")) {
                $sourceDir = [string]$entry["Source"]
                $destDir = [string]$entry["Destination"]
                $sourceFile = Join-Path $sourceDir $name
                $destFile = Join-Path $destDir $name
                if (-not (Test-Path -LiteralPath $sourceFile)) {
                    throw "Required EFI file missing: $sourceFile"
                }
                $relative = ("EFI\{0}\{1}" -f (Split-Path -Leaf $destDir), $name)
                Backup-RecoverixEfiTarget -EspRoot $espRoot -RelativePath $relative -DataRoot $DataRoot
                Copy-Item -LiteralPath $sourceFile -Destination $destFile -Force
                Write-InstallLog "Copied EFI file: $sourceFile -> $destFile"
            }
        }

        $recoveryBootGrubCfg = Join-Path $recoveryBootDest "grub.cfg"
        Render-RecoverixGrubConfig `
            -TemplatePath (Join-Path $recoveryBootSource "grub.cfg.template") `
            -DestinationPath $recoveryBootGrubCfg `
            -RecoveryUuid $PackageInfo.Uuid `
            -KernelVersion $PackageInfo.KernelVersion `
            -EspUuid $espUuid `
            -BootTimeoutSec "2"
        Write-InstallLog "Rendered RecoveryBoot grub.cfg: $recoveryBootGrubCfg"

        $directGrubCfg = Join-Path $directDest "grub.cfg"
        Render-RecoverixGrubConfig `
            -TemplatePath (Join-Path $directSource "grub.cfg.template") `
            -DestinationPath $directGrubCfg `
            -RecoveryUuid $PackageInfo.Uuid `
            -KernelVersion $PackageInfo.KernelVersion `
            -EspUuid $espUuid `
            -BootTimeoutSec "0"
        Write-InstallLog "Rendered RecoverixDirect grub.cfg: $directGrubCfg"

        foreach ($target in @(
            @{ Relative = "EFI\ubuntu\grub.cfg"; Destination = (Join-Path $ubuntuPrefixDest "grub.cfg") },
            @{ Relative = "EFI\Boot\grub.cfg"; Destination = (Join-Path $fallbackDest "grub.cfg") },
            @{ Relative = "boot\grub\grub.cfg"; Destination = (Join-Path $bootGrubDest "grub.cfg") },
            @{ Relative = "grub.cfg"; Destination = (Join-Path $espRoot "grub.cfg") }
        )) {
            $relative = [string]$target["Relative"]
            $destination = [string]$target["Destination"]
            Backup-RecoverixEfiTarget -EspRoot $espRoot -RelativePath $relative -DataRoot $DataRoot
            Render-RecoverixGrubBootstrapConfig -DestinationPath $destination -EspUuid $espUuid
            Write-InstallLog "Rendered Recoverix GRUB bootstrap: $destination"
        }

        foreach ($name in @("bootx64.efi", "grubx64.efi")) {
            $sourceFile = Join-Path $fallbackSource $name
            $destFile = Join-Path $fallbackDest $name
            if (-not (Test-Path -LiteralPath $sourceFile)) {
                throw "Required fallback EFI file missing: $sourceFile"
            }
            Backup-RecoverixEfiTarget -EspRoot $espRoot -RelativePath ("EFI\Boot\" + $name) -DataRoot $DataRoot
            Copy-Item -LiteralPath $sourceFile -Destination $destFile -Force
            Write-InstallLog "Copied fallback EFI file: $sourceFile -> $destFile"
        }

        Write-InstallLog "Recoverix EFI files installed to ESP $espRoot"
        return [PSCustomObject]@{
            EspPartitionNumber = $esp.PartitionNumber
            EspRoot = $espRoot
            EspUuid = $espUuid
            RecoveryBoot = "EFI\RecoveryBoot"
            RecoverixDirect = "EFI\RecoverixDirect"
            Fallback = "EFI\Boot\bootx64.efi"
        }
    } finally {
        if ($addedAccessPath) {
            Remove-RecoverixEspAccessPath -Esp $esp -EspRoot $espRoot
        }
        Remove-RecoverixTemporaryEspDriveLetter -Letter "S"
        if (-not (Test-Path -LiteralPath (Join-Path $espRoot "EFI"))) {
            Remove-Item -LiteralPath $espRoot -Force -ErrorAction SilentlyContinue
        } else {
            Write-InstallLog "ESP mount folder was left in place because it still appears mounted: $espRoot"
        }
    }
}

function New-RecoverixRecoveryPartitions {
    param(
        [object]$Plan,
        [object]$PackageInfo
    )

    $diskNumber = [int]$Plan.DiskNumber
    $windowsPartitionNumber = [int]$Plan.WindowsPartitionNumber
    $windowsPartition = Get-Partition -DiskNumber $diskNumber -PartitionNumber $windowsPartitionNumber
    $supported = Get-PartitionSupportedSize -DiskNumber $diskNumber -PartitionNumber $windowsPartitionNumber

    $linuxBytes = Align-BytesUp -Value ([int64]$PackageInfo.PartitionSizeBytes)
    $imageBytes = Align-BytesUp -Value ([int64]$Plan.RecoveryImageBytes)
    $reserveBytes = Align-BytesUp -Value $RecoveryTailReserveBytes
    $requiredBytes = $linuxBytes + $imageBytes + $reserveBytes
    $targetWindowsSize = Align-BytesDown -Value ([int64]$windowsPartition.Size - $requiredBytes)

    if ($targetWindowsSize -lt [int64]$supported.SizeMin) {
        throw ("Windows partition cannot be shrunk enough. Required={0}, shrink minimum={1}" -f (Format-BytesGiB $requiredBytes), (Format-BytesGiB $supported.SizeMin))
    }

    Write-Step "Shrinking Windows partition for Recoverix"
    Write-InstallLog ("Resize Windows partition: current={0}; target={1}; required={2}" -f $windowsPartition.Size, $targetWindowsSize, $requiredBytes)
    Resize-Partition -DiskNumber $diskNumber -PartitionNumber $windowsPartitionNumber -Size $targetWindowsSize
    Start-Sleep -Seconds 2
    Update-HostStorageCache

    $windowsPartition = Get-Partition -DiskNumber $diskNumber -PartitionNumber $windowsPartitionNumber
    $linuxOffset = Align-BytesUp -Value ([int64]$windowsPartition.Offset + [int64]$windowsPartition.Size + $reserveBytes)
    $imageOffset = Align-BytesUp -Value ($linuxOffset + $linuxBytes)

    Write-Step "Creating RECOVERY_LINUX partition"
    $linuxPartition = New-Partition -DiskNumber $diskNumber -Offset $linuxOffset -Size $linuxBytes -GptType $RecoveryLinuxGptType
    Start-Sleep -Seconds 1

    Write-Step "Creating RECOVERY_IMAGE partition"
    $imagePartition = New-Partition -DiskNumber $diskNumber -Offset $imageOffset -Size $imageBytes -GptType $MicrosoftBasicDataGptType
    Format-Volume -Partition $imagePartition -FileSystem NTFS -NewFileSystemLabel "RECOVERY_IMAGE" -Confirm:$false -Force | Out-Null
    Start-Sleep -Seconds 1
    try {
        $imageVolume = $imagePartition | Get-Volume -ErrorAction Stop
        if ($imageVolume.DriveLetter) {
            Remove-PartitionAccessPath `
                -DiskNumber $diskNumber `
                -PartitionNumber $imagePartition.PartitionNumber `
                -AccessPath ("{0}:\" -f $imageVolume.DriveLetter) `
                -ErrorAction SilentlyContinue
        }
    } catch {
        Write-InstallLog "WARNING: Could not remove RECOVERY_IMAGE drive letter: $($_.Exception.Message)"
    }
    try {
        Set-Partition -DiskNumber $diskNumber -PartitionNumber $imagePartition.PartitionNumber -NoDefaultDriveLetter $true -IsHidden $true
    } catch {
        Write-InstallLog "WARNING: Could not hide RECOVERY_IMAGE partition: $($_.Exception.Message)"
    }

    return [PSCustomObject]@{
        Linux = (Get-Partition -DiskNumber $diskNumber -PartitionNumber $linuxPartition.PartitionNumber)
        Image = (Get-Partition -DiskNumber $diskNumber -PartitionNumber $imagePartition.PartitionNumber)
        ReservedUnallocatedBytes = $reserveBytes
        RecoveryImageBytes = $imageBytes
        RecoveryLinuxBytes = $linuxBytes
    }
}

function Register-RecoverixBootIntegration {
    param(
        [string]$InstallRoot,
        [string]$WindowsPowerShell
    )

    if (-not $SkipTask) {
        $taskInstaller = Join-Path $InstallRoot "windows_agent\install_recoveryboot_monitor.ps1"
        if (Test-Path $taskInstaller) {
            Write-Step "Registering RecoveryBootMonitor scheduled task"
            Write-InstallLog "Registering RecoveryBootMonitor scheduled task."
            & $WindowsPowerShell -NoProfile -ExecutionPolicy Bypass -File $taskInstaller
            if ($LASTEXITCODE -ne 0) {
                throw "RecoveryBootMonitor registration failed with rc=$LASTEXITCODE"
            }
        }
    }

    if (-not $SkipNvram) {
        $writer = Join-Path $InstallRoot "native\nvram_writer\recoverix-nvram-writer.exe"
        if (Test-Path $writer) {
            Write-Step "Registering Recoverix boot entries"
            Write-InstallLog "Running Recoverix NVRAM repair."
            & $writer --skip-filesystem-extend
            if ($LASTEXITCODE -ne 0) {
                throw "Recoverix NVRAM repair failed with rc=$LASTEXITCODE"
            }
        }
    }
}

function Install-RecoverixRecoverySystem {
    param(
        [string]$Root,
        [object]$Plan,
        [string]$DataRoot,
        [string]$InstallRoot,
        [string]$WindowsPowerShell
    )

    if ($SkipRecoveryProvisioning) {
        Write-Step "Recovery partition provisioning skipped"
        Write-InstallLog "Recovery partition provisioning skipped by installer option."
        return $null
    }

    $packageInfo = Get-RecoverixRecoveryPackageInfo -Root $Root
    $diskNumber = [int]$Plan.DiskNumber
    $existing = Get-RecoverixRecoveryPartitions -DiskNumber $diskNumber -PackageInfo $packageInfo -Plan $Plan
    $linuxTarget = $existing.Linux
    $imageTarget = $existing.Image
    $recoveredPartialLinuxPartition = $false

    if ($null -eq $linuxTarget -and $null -ne $existing.BlankLinuxCandidate) {
        $linuxTarget = $existing.BlankLinuxCandidate
        $recoveredPartialLinuxPartition = $true
        Write-InstallLog ("Using unlabeled RECOVERY_LINUX candidate from a previous interrupted install: disk={0}; partition={1}; size={2}" -f $diskNumber, $linuxTarget.PartitionNumber, $linuxTarget.Size)
    }

    $createdPartitions = $false
    if ($null -eq $linuxTarget -and $null -eq $imageTarget) {
        if (-not (Confirm-RecoverixRecoveryProvisioning -Plan $Plan -PackageInfo $packageInfo -RecoveryImageBytes ([int64]$Plan.RecoveryImageBytes) -CreatesPartitions $true)) {
            Write-InstallLog "Recovery partition provisioning canceled by user."
            return "Canceled"
        }
        $partitionResult = New-RecoverixRecoveryPartitions -Plan $Plan -PackageInfo $packageInfo
        $linuxTarget = $partitionResult.Linux
        $imageTarget = $partitionResult.Image
        $createdPartitions = $true
    } elseif ($null -ne $linuxTarget -and $null -ne $imageTarget) {
        if (-not (Confirm-RecoverixRecoveryProvisioning -Plan $Plan -PackageInfo $packageInfo -RecoveryImageBytes ([int64]$Plan.RecoveryImageBytes) -CreatesPartitions $false)) {
            Write-InstallLog "Recovery runtime update canceled by user."
            return "Canceled"
        }
    } else {
        if ($null -ne $linuxTarget -and $null -eq $imageTarget) {
            throw "Partial Recoverix recovery partition state detected. RECOVERY_LINUX exists, but RECOVERY_IMAGE was not found. Remove the partial recovery partitions or restore RECOVERY_IMAGE before reinstalling."
        }
        throw "Partial Recoverix recovery partition state detected. RECOVERY_IMAGE exists, but RECOVERY_LINUX was not found. Remove the partial recovery partitions or restore RECOVERY_LINUX before reinstalling."
    }

    if ($null -eq $linuxTarget -or $null -eq $imageTarget) {
        throw "Recoverix recovery partition targets were not selected."
    }
    if ([int64]$linuxTarget.Size -lt [int64]$packageInfo.ImageSizeBytes) {
        throw "RECOVERY_LINUX partition is smaller than the packaged runtime image."
    }

    Copy-RecoverixImageToRawPartition -ImagePath $packageInfo.ImagePath -DiskNumber $diskNumber -PartitionNumber $linuxTarget.PartitionNumber
    Update-HostStorageCache
    Start-Sleep -Seconds 2

    $validated = Get-RecoverixRecoveryPartitions -DiskNumber $diskNumber -PackageInfo $packageInfo -Plan $Plan
    if ($null -eq $validated.Linux) {
        throw "RECOVERY_LINUX ext4 label was not detected after writing the packaged runtime image."
    }
    if ($null -eq $validated.Image) {
        throw "RECOVERY_IMAGE partition was not detected after recovery provisioning."
    }
    $linuxTarget = $validated.Linux
    $imageTarget = $validated.Image

    $efiResult = Install-RecoverixEfiFiles -Root $Root -Plan $Plan -PackageInfo $packageInfo -DataRoot $DataRoot
    Register-RecoverixBootIntegration -InstallRoot $InstallRoot -WindowsPowerShell $WindowsPowerShell

    $state = [PSCustomObject]@{
        provisioned_at_utc = (Get-Date).ToUniversalTime().ToString("o")
        created_partitions = $createdPartitions
        recovered_partial_linux_partition = $recoveredPartialLinuxPartition
        disk_number = $diskNumber
        recovery_linux_partition_number = $linuxTarget.PartitionNumber
        recovery_linux_partition_size_bytes = [int64]$linuxTarget.Size
        recovery_image_partition_number = $imageTarget.PartitionNumber
        recovery_image_partition_size_bytes = [int64]$imageTarget.Size
        recovery_linux_uuid = $packageInfo.Uuid
        recovery_kernel_version = $packageInfo.KernelVersion
        efi = $efiResult
    }
    $statePath = Join-Path $DataRoot "state\recovery-provisioning.json"
    $state | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -Path $statePath
    Write-InstallLog "Wrote recovery provisioning state: $statePath"
    return "Provisioned"
}

function Install-RecoverixWindowsPayload {
    param([string]$Root)

    $payload = Join-Path $Root "payload"
    $programFilesPayload = Join-Path $payload "program_files\Recoverix"
    $installRoot = Join-Path $env:ProgramFiles "Recoverix"
    $dataRoot = Join-Path $env:ProgramData "Recoverix"
    $windowsPowerShell = Get-WindowsPowerShellPath
    $installMode = Get-RecoverixInstallMode -InstallRoot $installRoot -DataRoot $dataRoot

    Write-Step ("Install mode: {0}" -f $installMode.Mode)
    Write-InstallLog ("Install mode: {0}; previous version: {1}; install root exists: {2}" -f $installMode.Mode, $installMode.InstalledVersion, $installMode.InstallRootExists)

    $installAction = Get-RecoverixInstallAction -InstallMode $installMode
    Write-InstallLog "Selected install action: $installAction"
    if ($installAction -eq "Cancel") {
        Write-Step "Recoverix setup canceled"
        Write-InstallLog "Recoverix setup canceled by user."
        return "Canceled"
    }
    if ($installAction -eq "AlreadyCurrent") {
        Write-Step "Recoverix is already up to date"
        Write-InstallLog "Recoverix is already up to date. Installed version: $($installMode.InstalledVersion)"
        return "AlreadyCurrent"
    }
    if ($installAction -eq "Uninstall") {
        Invoke-RecoverixPackageUninstall -Root $Root -InstallRoot $installRoot -WindowsPowerShell $windowsPowerShell
        return "Removed"
    }

    $rollbackBackup = ""
    try {
        Stop-RecoverixProcesses
        Reset-RecoverixDirectoryAccess -Path $installRoot
        Reset-RecoverixDirectoryAccess -Path $dataRoot
        foreach ($dir in @("logs", "state", "config")) {
            New-Item -ItemType Directory -Force -Path (Join-Path $dataRoot $dir) | Out-Null
        }
        if ($installMode.Mode -ne "FreshInstall") {
            $rollbackBackup = Backup-RecoverixProgramFiles -InstallRoot $installRoot -DataRoot $dataRoot
        }
        Remove-RecoverixDesktopShortcuts
        Clear-RecoverixProgramFiles -Path $installRoot

        Write-Step "Installing Windows payload to $installRoot"
        Write-InstallLog "Installing Windows payload to $installRoot"
        Copy-DirectoryContents -Source $programFilesPayload -Destination $installRoot
        Copy-RecoverixMaintenanceScripts -PackageRoot $Root -InstallRoot $installRoot
        Unblock-RecoverixInstalledFiles -Path $installRoot
        Write-InstallLog "Windows payload copied."

        $plan = Test-RecoverixPreflight
        $planPath = Join-Path $dataRoot "state\install-preflight.json"
        $plan | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -Path $planPath
        Write-Step "Wrote preflight state: $planPath"
        Write-InstallLog "Wrote preflight state: $planPath"
        $statePath = Write-RecoverixInstallState -DataRoot $dataRoot -InstallRoot $installRoot -InstallMode $installMode -Plan $plan
        Write-InstallLog "Wrote install state: $statePath"

        $recoveryResult = Install-RecoverixRecoverySystem `
            -Root $Root `
            -Plan $plan `
            -DataRoot $dataRoot `
            -InstallRoot $installRoot `
            -WindowsPowerShell $windowsPowerShell
        if ($recoveryResult -eq "Canceled") {
            Write-Step "Recoverix recovery provisioning canceled"
            Write-InstallLog "Recoverix recovery provisioning canceled by user."
            return "Canceled"
        }

        Register-RecoverixUninstallEntry -InstallRoot $installRoot
        Write-InstallLog "Registered Windows uninstall entry."

        $harden = Join-Path $installRoot "windows_agent\harden_permissions.cmd"
        if (Test-Path $harden) {
            Write-Step "Applying Recoverix file permissions"
            Write-InstallLog "Applying Recoverix file permissions."
            & $harden
            if ($LASTEXITCODE -eq 0) {
                Write-InstallLog "Recoverix file permissions applied."
            } else {
                Write-InstallLog "WARNING: Permission hardening failed with rc=$LASTEXITCODE"
            }
        }

        Unblock-RecoverixInstalledFiles -Path $installRoot

        if (-not $SkipStatusShortcut) {
            $shortcut = Join-Path $installRoot "StatusApp\create_desktop_shortcut.ps1"
            if (Test-Path $shortcut) {
                Write-Step "Creating Recoverix desktop shortcut"
                Write-InstallLog "Creating Recoverix desktop shortcut."
                & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File $shortcut
                if ($LASTEXITCODE -eq 0) {
                    Write-InstallLog "Recoverix desktop shortcut created."
                } else {
                    Write-InstallLog "WARNING: Desktop shortcut creation failed with rc=$LASTEXITCODE"
                }
            } else {
                Write-InstallLog "WARNING: Desktop shortcut script was not found: $shortcut"
            }
        }

    } catch {
        Write-InstallLog "ERROR during payload install: $($_.Exception.Message)"
        Restore-RecoverixProgramFiles -BackupPath $rollbackBackup -InstallRoot $installRoot
        throw
    }

    Write-Step "Windows payload installation complete"
    Write-InstallLog "Windows payload installation complete."
    return "Installed"
}

try {
    $root = Resolve-PackageRoot -Root $PackageRoot
    Import-RecoverixPackageMetadata -Root $root
    Write-InstallLog "$ProductName $ProductVersion $EffectivePackageType installer started. Package root: $root"
    Write-Step "$ProductName $ProductVersion $EffectivePackageType installer"
    Write-Step "Package root: $root"

    if ($PreflightOnly) {
        $plan = Test-RecoverixPreflight
        $plan | ConvertTo-Json -Depth 5
        exit 0
    }

    $result = Install-RecoverixWindowsPayload -Root $root
    if ($result -eq "Canceled") {
        Write-InstallLog "$ProductName setup canceled."
        exit 1223
    }
    Write-InstallLog "$ProductName setup completed. Result=$result"
    exit 0
} catch {
    Write-InstallLog ("ERROR: " + $_.Exception.Message)
    Write-Error $_.Exception.Message
    exit 1
}

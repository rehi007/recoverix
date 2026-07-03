$ErrorActionPreference = "Stop"

$target = Join-Path $env:ProgramFiles "Recoverix\StatusApp\RecoverixStatus.exe"
if (-not (Test-Path $target)) {
    throw "Target executable was not found: $target"
}

$target = (Resolve-Path -LiteralPath $target).Path
$unblock = Get-Command Unblock-File -ErrorAction SilentlyContinue
if ($unblock) {
    Unblock-File -LiteralPath $target -ErrorAction SilentlyContinue
}

$shortcutName = "Recoverix " + [string][char]0xC0C1 + [string][char]0xD0DC + " " + [string][char]0xD655 + [string][char]0xC778 + ".lnk"
$legacyShortcutNames = @(
    $shortcutName,
    "Recoverix Status.lnk",
    "RecoverixStatus.lnk"
)

function New-RecoverixShortcut {
    param(
        [string]$DesktopPath
    )

    if ([string]::IsNullOrWhiteSpace($DesktopPath) -or -not (Test-Path $DesktopPath)) {
        return $false
    }

    foreach ($name in $legacyShortcutNames) {
        $oldShortcut = Join-Path $DesktopPath $name
        if (Test-Path -LiteralPath $oldShortcut) {
            Remove-Item -LiteralPath $oldShortcut -Force -ErrorAction SilentlyContinue
        }
    }

    $shortcutPath = Join-Path $DesktopPath $shortcutName
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = Split-Path $target
    $shortcut.IconLocation = "$target,0"
    $shortcut.Description = "Recoverix Status"
    $shortcut.Save()

    if ($unblock -and (Test-Path -LiteralPath $shortcutPath)) {
        Unblock-File -LiteralPath $shortcutPath -ErrorAction SilentlyContinue
    }

    Write-Host "Shortcut created: $shortcutPath"
    return $true
}

$created = $false
$desktopCandidates = @(
    [Environment]::GetFolderPath("DesktopDirectory")
)

foreach ($desktop in ($desktopCandidates | Select-Object -Unique)) {
    try {
        if (New-RecoverixShortcut -DesktopPath $desktop) {
            $created = $true
        }
    } catch {
        Write-Warning ("Shortcut creation failed for {0}: {1}" -f $desktop, $_.Exception.Message)
    }
}

if (-not $created) {
    throw "Recoverix desktop shortcut could not be created."
}

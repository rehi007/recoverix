$ErrorActionPreference = "Stop"

$target = Join-Path $env:ProgramFiles "Recoverix\StatusApp\RecoverixStatus.exe"
if (-not (Test-Path $target)) {
    throw "Target executable was not found: $target"
}

$desktop = [Environment]::GetFolderPath("CommonDesktopDirectory")
if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path $desktop)) {
    $desktop = [Environment]::GetFolderPath("DesktopDirectory")
}

$shortcutPath = Join-Path $desktop "Recoverix 상태 확인.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = Split-Path $target
$shortcut.IconLocation = "$target,0"
$shortcut.Description = "Recoverix 상태 확인"
$shortcut.Save()

Write-Host "Shortcut created: $shortcutPath"

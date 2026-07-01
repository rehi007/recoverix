@echo off
setlocal

set "APP_DIR=%ProgramFiles%\Recoverix\StatusApp"
set "SOURCE_DIR=%~dp0"

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo This installer must be run as Administrator.
  echo Right-click install_status_app.cmd and select "Run as administrator".
  pause
  exit /b 1
)

if not exist "%SOURCE_DIR%RecoverixStatus.exe" (
  echo RecoverixStatus.exe was not found next to this installer.
  pause
  exit /b 2
)

if exist "%SOURCE_DIR%verify_signature.ps1" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SOURCE_DIR%verify_signature.ps1" -Path "%SOURCE_DIR%RecoverixStatus.exe" -AllowUnsigned
  if not "%errorlevel%"=="0" (
    echo RecoverixStatus.exe signature verification failed.
    pause
    exit /b 5
  )
)

if not exist "%APP_DIR%" mkdir "%APP_DIR%"
copy /Y "%SOURCE_DIR%RecoverixStatus.exe" "%APP_DIR%\RecoverixStatus.exe" >nul
if not "%errorlevel%"=="0" (
  echo Failed to copy RecoverixStatus.exe to "%APP_DIR%".
  pause
  exit /b 3
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SOURCE_DIR%create_desktop_shortcut.ps1"
if not "%errorlevel%"=="0" (
  echo Failed to create desktop shortcut.
  pause
  exit /b 4
)

echo Recoverix Status App installed.
echo Installed path: %APP_DIR%\RecoverixStatus.exe
pause

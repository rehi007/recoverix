@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%.") do set "INSTALL_ROOT=%%~fI"

set "POWERSHELL_EXE=%SystemRoot%\SysNative\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%POWERSHELL_EXE%" (
  set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
)

if not exist "%POWERSHELL_EXE%" (
  set "POWERSHELL_EXE=powershell.exe"
)

net session >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
  "%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

set "TEMP_PS1=%TEMP%\RecoverixUninstall-%RANDOM%%RANDOM%.ps1"
copy /Y "%SCRIPT_DIR%uninstall_recoverix.ps1" "%TEMP_PS1%" >nul
if not "%ERRORLEVEL%"=="0" (
  echo ERROR: Failed to stage Recoverix uninstaller.
  exit /b 2
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%TEMP_PS1%" -InstallRoot "%INSTALL_ROOT%" %*
set "RC=%ERRORLEVEL%"
del "%TEMP_PS1%" >nul 2>&1
exit /b %RC%

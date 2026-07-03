@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PACKAGE_ROOT=%SCRIPT_DIR%"
set "INSTALL_PS1=%SCRIPT_DIR%install_recoverix.ps1"
set "POWERSHELL_EXE=%SystemRoot%\SysNative\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%POWERSHELL_EXE%" (
  set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
)

if not exist "%POWERSHELL_EXE%" (
  set "POWERSHELL_EXE=powershell.exe"
)

if not exist "%INSTALL_PS1%" (
  set "INSTALL_PS1=%SCRIPT_DIR%installer\windows\install_recoverix.ps1"
)

if not exist "%INSTALL_PS1%" (
  echo ERROR: install_recoverix.ps1 was not found.
  exit /b 2
)

"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_PS1%" %*
exit /b %ERRORLEVEL%

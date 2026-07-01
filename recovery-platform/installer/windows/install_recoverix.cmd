@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PACKAGE_ROOT=%SCRIPT_DIR%..\.."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_recoverix.ps1" -PackageRoot "%PACKAGE_ROOT%" %*
exit /b %ERRORLEVEL%

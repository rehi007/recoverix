@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall_recoverix.ps1" %*
exit /b %ERRORLEVEL%

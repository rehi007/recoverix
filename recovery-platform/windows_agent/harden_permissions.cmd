@echo off
setlocal

net session >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
  echo ERROR: Run this script as Administrator.
  exit /b 3
)

set "RECOVERIX_ROOT=%ProgramFiles%\Recoverix"
set "RECOVERIX_DATA=%ProgramData%\Recoverix"

if not exist "%RECOVERIX_ROOT%" (
  echo ERROR: Recoverix install root was not found: %RECOVERIX_ROOT%
  exit /b 4
)

if not exist "%RECOVERIX_DATA%" mkdir "%RECOVERIX_DATA%"
if not exist "%RECOVERIX_DATA%\logs" mkdir "%RECOVERIX_DATA%\logs"
if not exist "%RECOVERIX_DATA%\state" mkdir "%RECOVERIX_DATA%\state"
if not exist "%RECOVERIX_DATA%\config" mkdir "%RECOVERIX_DATA%\config"

echo Applying Recoverix permissions...

icacls "%RECOVERIX_ROOT%" /inheritance:r >nul
icacls "%RECOVERIX_ROOT%" /grant:r "SYSTEM:(OI)(CI)(F)" "Administrators:(OI)(CI)(RX)" "Users:(OI)(CI)(RX)" >nul
icacls "%RECOVERIX_ROOT%" /setowner "SYSTEM" /T /C >nul

icacls "%RECOVERIX_DATA%" /inheritance:r >nul
icacls "%RECOVERIX_DATA%" /grant:r "SYSTEM:(OI)(CI)(F)" "Administrators:(OI)(CI)(R)" "Users:(OI)(CI)(R)" >nul
icacls "%RECOVERIX_DATA%" /setowner "SYSTEM" /T /C >nul

echo Recoverix permissions applied.
exit /b 0

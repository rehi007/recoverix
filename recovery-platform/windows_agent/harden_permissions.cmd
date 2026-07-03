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

set "LOG=%RECOVERIX_DATA%\logs\harden_permissions.log"
set "FAILED=0"

echo [%DATE% %TIME%] Applying Recoverix permissions.>>"%LOG%"

call :run icacls "%RECOVERIX_ROOT%" /inheritance:e /T /C
call :run icacls "%RECOVERIX_ROOT%" /grant "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "*S-1-5-32-545:(OI)(CI)(RX)" /T /C

call :run icacls "%RECOVERIX_DATA%" /inheritance:e /T /C
call :run icacls "%RECOVERIX_DATA%" /grant "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" "*S-1-5-32-545:(OI)(CI)(R)" /T /C

echo Recoverix permissions applied.
exit /b 0

:run
echo ^> %*>>"%LOG%"
%* >>"%LOG%" 2>&1
if errorlevel 1 (
  echo WARNING: command failed with rc=%ERRORLEVEL%>>"%LOG%"
  set "FAILED=1"
)
exit /b 0

@echo off
setlocal

net session >nul 2>&1
if not "%ERRORLEVEL%"=="0" (
  echo ERROR: Run this script as Administrator.
  exit /b 3
)

set "RECOVERIX_ROOT=%ProgramFiles%\Recoverix"
if not exist "%RECOVERIX_ROOT%" (
  echo ERROR: Recoverix install root was not found: %RECOVERIX_ROOT%
  exit /b 4
)

pushd "%RECOVERIX_ROOT%" >nul

set "NVRAM_WRITER=%RECOVERIX_ROOT%\native\nvram_writer\recoverix-nvram-writer.exe"
if exist "%NVRAM_WRITER%" (
  schtasks /Create /TN RecoveryBootMonitor /SC ONSTART /DELAY 0001:00 /RU SYSTEM /RL HIGHEST /TR "\"%NVRAM_WRITER%\"" /F
) else (
  set "PYTHONPATH=%RECOVERIX_ROOT%;%PYTHONPATH%"

  where py >nul 2>&1
  if "%ERRORLEVEL%"=="0" (
    py -3 -m windows_agent.install_task --working-directory "%RECOVERIX_ROOT%"
  ) else (
    where python >nul 2>&1
    if not "%ERRORLEVEL%"=="0" (
      echo ERROR: native writer and Python were not found.
      echo Expected: %NVRAM_WRITER%
      popd >nul
      exit /b 2
    )
    python -m windows_agent.install_task --working-directory "%RECOVERIX_ROOT%"
  )
)

set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo ERROR: RecoveryBootMonitor registration failed. rc=%RC%
  popd >nul
  exit /b %RC%
)

if exist "%RECOVERIX_ROOT%\windows_agent\harden_permissions.cmd" (
  call "%RECOVERIX_ROOT%\windows_agent\harden_permissions.cmd"
  if not "%ERRORLEVEL%"=="0" (
    echo ERROR: Recoverix permission hardening failed. rc=%ERRORLEVEL%
    popd >nul
    exit /b %ERRORLEVEL%
  )
)

echo.
echo RecoveryBootMonitor registration result:
schtasks /Query /TN RecoveryBootMonitor /V /FO LIST
set "RC=%ERRORLEVEL%"

popd >nul
exit /b %RC%

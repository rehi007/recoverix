$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error 'Run this script as Administrator.'
    exit 3
}

$recoverixRoot = Join-Path $env:ProgramFiles 'Recoverix'
if (-not (Test-Path $recoverixRoot)) {
    Write-Error "Recoverix install root was not found: $recoverixRoot"
    exit 4
}
$recoverixRoot = (Resolve-Path $recoverixRoot).Path
Set-Location $recoverixRoot

$nvramWriter = Join-Path $recoverixRoot 'native\nvram_writer\recoverix-nvram-writer.exe'
if (Test-Path $nvramWriter) {
    $action = New-ScheduledTaskAction -Execute $nvramWriter
    $boot = New-ScheduledTaskTrigger -AtStartup
    $boot.Delay = 'PT1M'
    $principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName 'RecoveryBootMonitor' -Action $action -Trigger $boot -Principal $principal -Force | Out-Null
} else {
    $env:PYTHONPATH = "$recoverixRoot;$env:PYTHONPATH"

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3 -m windows_agent.install_task --working-directory $recoverixRoot
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) {
            Write-Error "Native writer and Python were not found. Expected: $nvramWriter"
            exit 2
        }
        & python -m windows_agent.install_task --working-directory $recoverixRoot
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Error "RecoveryBootMonitor registration failed. rc=$LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

$harden = Join-Path $recoverixRoot 'windows_agent\harden_permissions.cmd'
if (Test-Path $harden) {
    & $harden
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Recoverix permission hardening failed. rc=$LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

Write-Host ''
Write-Host 'RecoveryBootMonitor registration result:'
schtasks /Query /TN RecoveryBootMonitor /V /FO LIST
exit $LASTEXITCODE

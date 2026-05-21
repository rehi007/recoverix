"""Scheduled task registration helpers (RecoveryBootMonitor)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from common.logger import get_logger
from windows_agent.logging_config import agent_logger, append_log, ensure_agent_file_logging

logger = get_logger(__name__)

TASK_NAME = "RecoveryBootMonitor"


def build_register_task_script(
    *,
    python_executable: Path,
    working_directory: Path,
) -> str:
    """Return a PowerShell script body (single -Command string)."""
    py = str(python_executable.resolve()).replace("'", "''")
    wd = str(working_directory.resolve()).replace("'", "''")
    # Boot: recovery after power-on / BIOS updates. Periodic: detect post-Windows Update drift.
    return "; ".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$cmd = '{py}'",
            f"$wd = '{wd}'",
            "$arg = '-m windows_agent.agent --once'",
            "$action = New-ScheduledTaskAction -Execute $cmd -Argument $arg -WorkingDirectory $wd",
            "$boot = New-ScheduledTaskTrigger -AtStartup",
            "$start = (Get-Date).AddMinutes(5)",
            "$periodic = New-ScheduledTaskTrigger -Once -At $start "
            "-RepetitionInterval (New-TimeSpan -Hours 6) "
            "-RepetitionDuration (New-TimeSpan -Days 3650)",
            "$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-18' "
            "-LogonType ServiceAccount -RunLevel Highest",
            f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action "
            "-Trigger @($boot, $periodic) -Principal $principal -Force",
        ]
    )


def register_recovery_boot_monitor_task(
    *,
    python_executable: Path,
    working_directory: Optional[Path] = None,
    dry_run: bool = False,
) -> int:
    """Create the RecoveryBootMonitor task (SYSTEM, highest privileges, hidden-capable settings)."""
    ensure_agent_file_logging()
    wd = working_directory or Path.cwd()
    append_log(
        "windows_agent.log",
        f"register_recovery_boot_monitor_task dry_run={dry_run} exe={python_executable} wd={wd}",
    )

    if dry_run:
        agent_logger().info("[DRY-RUN] would register %s", TASK_NAME)
        return 0

    if sys.platform != "win32":
        logger.error("task registration requires Windows")
        return 2

    script = build_register_task_script(python_executable=python_executable, working_directory=wd)
    argv: List[str] = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        agent_logger().error("task install failed: %s", proc.stderr.strip())
        append_log("error.log", f"Register-ScheduledTask failed rc={proc.returncode}: {proc.stderr!r}")
        return 1

    agent_logger().info("%s registered", TASK_NAME)
    return 0

"""CLI entry to install RecoveryBootMonitor (delegates to task_scheduler)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from common.logger import get_logger, setup_logging
from windows_agent.task_scheduler import register_recovery_boot_monitor_task
from windows_agent.task_scheduler import RECOVERIX_INSTALL_ROOT

logger = get_logger(__name__)


def main(argv: Optional[List[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Install RecoveryBootMonitor scheduled task")
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python interpreter to run the agent module",
    )
    parser.add_argument(
        "--working-directory",
        type=Path,
        default=RECOVERIX_INSTALL_ROOT,
        help="Working directory for the task (default: C:\\Program Files\\Recoverix)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log only; do not register the task",
    )
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        logger.error("install_task requires Windows")
        return 2

    return register_recovery_boot_monitor_task(
        python_executable=args.python,
        working_directory=args.working_directory,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

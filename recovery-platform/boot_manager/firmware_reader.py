"""Read-only UEFI firmware boot entry analysis via bcdedit."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from boot_manager.boot_entry import FirmwareAnalysisResult, analyze_firmware_output
from common.command import run_command, run_readonly
from common.logger import get_logger, setup_logging

logger = get_logger(__name__)

_BCDEDIT_FIRMWARE = ["bcdedit", "/enum", "firmware"]


def _run_bcdedit(*, dry_run: bool) -> str:
    if dry_run:
        result = run_command(_BCDEDIT_FIRMWARE, dry_run=True)
        return ""

    result = run_readonly(_BCDEDIT_FIRMWARE)
    if result.returncode != 0:
        logger.warning("bcdedit firmware enum failed: %s", result.stderr.strip())
        return ""
    return result.stdout


def read_firmware_boot(*, dry_run: bool = True) -> FirmwareAnalysisResult:
    """Collect and analyze firmware boot entries (read-only)."""
    if sys.platform != "win32":
        logger.warning("firmware reader requires Windows; found %s", sys.platform)
        return FirmwareAnalysisResult(
            windows_boot_manager=None,
            recovery_boot=None,
            boot_order=[],
            boot_next=None,
            entries=[],
            status="FAIL",
            dry_run=dry_run,
        )

    if dry_run:
        _run_bcdedit(dry_run=True)
        logger.info("dry-run mode: skipping firmware boot analysis")
        return FirmwareAnalysisResult(
            windows_boot_manager=None,
            recovery_boot=None,
            boot_order=[],
            boot_next=None,
            entries=[],
            status="FAIL",
            dry_run=True,
        )

    output = _run_bcdedit(dry_run=False)
    if not output.strip():
        logger.warning("empty bcdedit firmware output")
        return FirmwareAnalysisResult(
            windows_boot_manager=None,
            recovery_boot=None,
            boot_order=[],
            boot_next=None,
            entries=[],
            status="FAIL",
            dry_run=False,
        )

    return analyze_firmware_output(output, dry_run=False)


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Read-only UEFI firmware boot entry analysis",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute read-only bcdedit (default: dry-run)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = read_firmware_boot(dry_run=not args.live)
    print(result.to_json())
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Windows preflight CLI — read-only system checks."""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from typing import List, Optional

from common.command import run_readonly
from common.logger import get_logger, setup_logging
from validation.system_check import (
    SystemCheckResult,
    merge_bitlocker_states,
    parse_bcdedit_firmware,
    parse_bitlocker_volumes_json,
    parse_manage_bde_status,
    parse_partition_style,
    parse_secure_boot_confirm,
    run_system_check,
)

logger = get_logger(__name__)

_POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


class WindowsSystemProbes:
    """Read-only Windows probes for system preflight."""

    def is_windows(self) -> bool:
        return sys.platform == "win32"

    def is_admin(self) -> bool:
        if not self.is_windows():
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False

    def boot_mode(self) -> str:
        result = run_readonly(["bcdedit", "/enum", "firmware"])
        if result.returncode == 0 and result.stdout.strip():
            mode = parse_bcdedit_firmware(result.stdout)
            if mode == "UEFI":
                return "UEFI"

        ps = run_readonly(
            _POWERSHELL
            + [
                "if (Test-Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot\\State') "
                "{ 'UEFI' } else { 'LEGACY' }"
            ]
        )
        if ps.returncode == 0:
            value = ps.stdout.strip().upper()
            if value in ("UEFI", "LEGACY"):
                return value
        return "UNKNOWN"

    def partition_style(self) -> str:
        script = (
            "(Get-Disk | Where-Object { $_.IsBoot -eq $true } "
            "| Select-Object -First 1 -ExpandProperty PartitionStyle)"
        )
        result = run_readonly(_POWERSHELL + [script])
        if result.returncode != 0:
            return "UNKNOWN"
        return parse_partition_style(result.stdout)

    def bitlocker_state(self) -> str:
        manage = run_readonly(["manage-bde", "-status"])
        manage_state = (
            parse_manage_bde_status(manage.stdout)
            if manage.returncode == 0
            else None
        )

        ps = run_readonly(
            _POWERSHELL
            + ["Get-BitLockerVolume | Select-Object VolumeStatus, ProtectionStatus | ConvertTo-Json"]
        )
        ps_state = (
            parse_bitlocker_volumes_json(ps.stdout)
            if ps.returncode == 0
            else None
        )

        return merge_bitlocker_states(manage_state, ps_state)

    def secure_boot_state(self) -> str:
        if self.boot_mode() != "UEFI":
            return "N/A"

        result = run_readonly(_POWERSHELL + ["Confirm-SecureBootUEFI"])
        combined = f"{result.stdout}\n{result.stderr}"
        return parse_secure_boot_confirm(combined, returncode=result.returncode)


def run_preflight() -> SystemCheckResult:
    """Execute Windows preflight checks."""
    return run_system_check(WindowsSystemProbes())


def main(argv: Optional[List[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Windows recovery preflight checks")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print results as JSON",
    )
    args = parser.parse_args(argv)

    result = run_preflight()

    if args.json:
        print(result.to_json())
    else:
        print(json.dumps(result.to_dict(), indent=2))

    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

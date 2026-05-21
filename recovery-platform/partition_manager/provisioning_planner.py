"""Dry-run Recovery partition provisioning plan (no shrink/create)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from common.command import run_command, run_readonly
from common.logger import get_logger, setup_logging
from partition_manager.discovery import discover_partitions
from partition_manager.models import DiscoveryResult, PartitionState
from validation.system_check import (
    merge_bitlocker_states,
    parse_bitlocker_volumes_json,
    parse_manage_bde_status,
)

logger = get_logger(__name__)

_POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

BACKUP_SIZE_FACTOR = 0.7
RECOVERY_IMAGE_HEADROOM_FACTOR = 1.25
RECOVERY_LINUX_MIN_GB = 4
RECOVERY_LINUX_MAX_GB = 8
RECOVERY_LINUX_DEFAULT_GB = 8

_USAGE_SCRIPT = r"""
$win = Get-Partition | Where-Object { $_.DriveLetter } | ForEach-Object {
    $vol = Get-Volume -DriveLetter $_.DriveLetter -ErrorAction SilentlyContinue
  if ($vol -and $vol.FileSystem -eq 'NTFS' -and (Test-Path ($_.DriveLetter + ':\Windows\System32\config\SYSTEM'))) {
    $_
  }
} | Select-Object -First 1
if (-not $win) { return @{} }
$disk = Get-Disk -Number $win.DiskNumber
$vol = Get-Volume -DriveLetter $win.DriveLetter
$parts = Get-Partition -DiskNumber $disk.Number
$allocated = ($parts | Measure-Object -Property Size -Sum).Sum
@{
  disk_number = $disk.Number
  partition_number = $win.PartitionNumber
  drive_letter = $win.DriveLetter
  volume_size = $vol.Size
  volume_size_remaining = $vol.SizeRemaining
  disk_size = $disk.Size
  allocated_size = $allocated
} | ConvertTo-Json -Compress
"""


@dataclass(frozen=True)
class WindowsDiskUsage:
    """Read-only Windows boot volume usage metrics."""

    disk_number: int
    partition_number: int
    drive_letter: str
    volume_size_bytes: int
    volume_used_bytes: int
    volume_free_bytes: int
    disk_size_bytes: int
    unallocated_bytes: int


@dataclass(frozen=True)
class ProvisioningPlan:
    """Recovery partition provisioning plan (plan-only, never executed)."""

    windows_used_gb: int
    estimated_backup_gb: int
    recommended_recovery_image_gb: int
    recommended_recovery_linux_gb: int
    shrinkable_gb: int
    can_provision: bool
    dry_run: bool = True
    status: str = "FAIL"
    bitlocker: str = "UNKNOWN"
    planned_actions: List[str] = field(default_factory=list)
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def bytes_to_gb(value: int) -> int:
    """Convert bytes to whole gibibytes (rounded up)."""
    if value <= 0:
        return 0
    gib = 1024**3
    return int(math.ceil(value / gib))


def read_bitlocker_state(*, live: bool) -> str:
    if not live or sys.platform != "win32":
        return "UNKNOWN"

    manage = run_readonly(["manage-bde", "-status"])
    manage_state = (
        parse_manage_bde_status(manage.stdout) if manage.returncode == 0 else None
    )
    ps = run_readonly(
        _POWERSHELL
        + ["Get-BitLockerVolume | Select-Object VolumeStatus, ProtectionStatus | ConvertTo-Json"]
    )
    ps_state = (
        parse_bitlocker_volumes_json(ps.stdout) if ps.returncode == 0 else None
    )
    return merge_bitlocker_states(manage_state, ps_state)


def parse_usage_payload(payload: str) -> Optional[WindowsDiskUsage]:
    if not payload.strip():
        return None
    data = json.loads(payload)
    if not data:
        return None

    volume_size = int(data["volume_size"])
    volume_remaining = int(data["volume_size_remaining"])
    disk_size = int(data["disk_size"])
    allocated = int(data["allocated_size"])
    used = volume_size - volume_remaining
    unallocated = max(disk_size - allocated, 0)

    return WindowsDiskUsage(
        disk_number=int(data["disk_number"]),
        partition_number=int(data["partition_number"]),
        drive_letter=str(data["drive_letter"]).rstrip(":"),
        volume_size_bytes=volume_size,
        volume_used_bytes=used,
        volume_free_bytes=volume_remaining,
        disk_size_bytes=disk_size,
        unallocated_bytes=unallocated,
    )


def collect_windows_disk_usage(*, live: bool) -> Optional[WindowsDiskUsage]:
    """Collect Windows boot disk usage via read-only PowerShell."""
    if not live or sys.platform != "win32":
        return None

    result = run_readonly(_POWERSHELL + [_USAGE_SCRIPT])
    if result.returncode != 0:
        logger.warning("usage query failed: %s", result.stderr.strip())
        return None
    return parse_usage_payload(result.stdout)


def _validate_single_windows_disk(discovery: DiscoveryResult) -> Optional[str]:
    windows = discovery.windows_partition
    if windows is None:
        return "Windows OS partition not found"
    if windows.state == PartitionState.UNSUPPORTED.value:
        return windows.reason or "Multiple Windows OS partitions detected"
    if windows.state != PartitionState.FOUND.value:
        return windows.reason or "Windows OS partition not uniquely identified"
    return None


def _recovery_partitions_present(discovery: DiscoveryResult) -> bool:
    for record in (discovery.recovery_image_partition, discovery.recovery_linux_partition):
        if record is not None and record.state == PartitionState.FOUND.value:
            return True
    return False


def recommend_recovery_linux_gb(*, shrinkable_gb: int, required_image_gb: int) -> int:
    """Pick Recovery Linux size within 4–8 GiB policy range."""
    remaining = shrinkable_gb - required_image_gb
    if remaining >= RECOVERY_LINUX_MAX_GB:
        return RECOVERY_LINUX_MAX_GB
    if remaining >= RECOVERY_LINUX_MIN_GB:
        return max(RECOVERY_LINUX_MIN_GB, remaining)
    return RECOVERY_LINUX_DEFAULT_GB


def calculate_sizes(usage: WindowsDiskUsage) -> Dict[str, int]:
    """Derive backup and Recovery partition size recommendations."""
    windows_used_gb = bytes_to_gb(usage.volume_used_bytes)
    estimated_backup_gb = int(round(windows_used_gb * BACKUP_SIZE_FACTOR))
    recommended_recovery_image_gb = int(
        math.ceil(estimated_backup_gb * RECOVERY_IMAGE_HEADROOM_FACTOR)
    )
    shrinkable_gb = bytes_to_gb(usage.volume_free_bytes + usage.unallocated_bytes)
    recommended_recovery_linux_gb = recommend_recovery_linux_gb(
        shrinkable_gb=shrinkable_gb,
        required_image_gb=recommended_recovery_image_gb,
    )
    return {
        "windows_used_gb": windows_used_gb,
        "estimated_backup_gb": estimated_backup_gb,
        "recommended_recovery_image_gb": recommended_recovery_image_gb,
        "recommended_recovery_linux_gb": recommended_recovery_linux_gb,
        "shrinkable_gb": shrinkable_gb,
    }


def build_provisioning_plan(
    *,
    usage: Optional[WindowsDiskUsage],
    discovery: DiscoveryResult,
    bitlocker: str,
    dry_run: bool = True,
) -> ProvisioningPlan:
    """Build a dry-run Recovery partition provisioning plan."""
    planned_actions: List[str] = [
        "collect Windows boot disk usage (read-only)",
        "calculate estimated backup size (used_size × 0.7)",
        "calculate Recovery Image / Recovery Linux recommendations",
        "calculate shrinkable space (shrink only after user confirmation)",
    ]

    windows_error = _validate_single_windows_disk(discovery)
    if windows_error:
        return ProvisioningPlan(
            windows_used_gb=0,
            estimated_backup_gb=0,
            recommended_recovery_image_gb=0,
            recommended_recovery_linux_gb=RECOVERY_LINUX_DEFAULT_GB,
            shrinkable_gb=0,
            can_provision=False,
            dry_run=dry_run,
            status="FAIL",
            bitlocker=bitlocker,
            planned_actions=planned_actions,
            reason=windows_error,
        )

    if bitlocker == "ON":
        return ProvisioningPlan(
            windows_used_gb=0,
            estimated_backup_gb=0,
            recommended_recovery_image_gb=0,
            recommended_recovery_linux_gb=RECOVERY_LINUX_DEFAULT_GB,
            shrinkable_gb=0,
            can_provision=False,
            dry_run=dry_run,
            status="FAIL",
            bitlocker=bitlocker,
            planned_actions=planned_actions,
            reason="BitLocker is ON; provisioning refused",
        )

    if _recovery_partitions_present(discovery):
        return ProvisioningPlan(
            windows_used_gb=0,
            estimated_backup_gb=0,
            recommended_recovery_image_gb=0,
            recommended_recovery_linux_gb=RECOVERY_LINUX_DEFAULT_GB,
            shrinkable_gb=0,
            can_provision=False,
            dry_run=dry_run,
            status="FAIL",
            bitlocker=bitlocker,
            planned_actions=planned_actions,
            reason="Recovery partition(s) already exist",
        )

    if usage is None:
        return ProvisioningPlan(
            windows_used_gb=0,
            estimated_backup_gb=0,
            recommended_recovery_image_gb=0,
            recommended_recovery_linux_gb=RECOVERY_LINUX_DEFAULT_GB,
            shrinkable_gb=0,
            can_provision=False,
            dry_run=dry_run,
            status="FAIL",
            bitlocker=bitlocker,
            planned_actions=planned_actions,
            reason="Windows disk usage data unavailable",
        )

    sizes = calculate_sizes(usage)
    required_gb = (
        sizes["recommended_recovery_image_gb"] + sizes["recommended_recovery_linux_gb"]
    )
    can_provision = sizes["shrinkable_gb"] >= required_gb

    planned_actions.extend(
        [
            "await explicit user confirmation before any shrink",
            f"plan shrink Windows partition on disk {usage.disk_number} (not executed)",
            "plan create RECOVERY_IMAGE partition (not executed)",
            "plan create RECOVERY_LINUX partition (not executed)",
        ]
    )

    reason = None
    status = "PASS"
    if not can_provision:
        status = "FAIL"
        reason = (
            f"insufficient shrinkable space: need {required_gb} GiB, "
            f"available {sizes['shrinkable_gb']} GiB"
        )

    return ProvisioningPlan(
        windows_used_gb=sizes["windows_used_gb"],
        estimated_backup_gb=sizes["estimated_backup_gb"],
        recommended_recovery_image_gb=sizes["recommended_recovery_image_gb"],
        recommended_recovery_linux_gb=sizes["recommended_recovery_linux_gb"],
        shrinkable_gb=sizes["shrinkable_gb"],
        can_provision=can_provision,
        dry_run=dry_run,
        status=status,
        bitlocker=bitlocker,
        planned_actions=planned_actions,
        reason=reason,
    )


def create_provisioning_plan(*, live: bool = False, dry_run: bool = True) -> ProvisioningPlan:
    """Discover layout, read BitLocker, and build provisioning plan."""
    if dry_run and not live:
        run_command(_POWERSHELL + [_USAGE_SCRIPT], dry_run=True)

    discovery = discover_partitions(dry_run=not live)
    bitlocker = read_bitlocker_state(live=live)
    usage = collect_windows_disk_usage(live=live)
    return build_provisioning_plan(
        usage=usage,
        discovery=discovery,
        bitlocker=bitlocker,
        dry_run=dry_run,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Generate Recovery partition provisioning plan (dry-run only)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Read live Windows disk metrics (still plan-only)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = create_provisioning_plan(live=args.live, dry_run=True)
    print(plan.to_json())
    return 0 if plan.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

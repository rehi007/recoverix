"""Prepare the Windows partition boundary for partclone restore.

Only adjacent free space after the Windows partition is used. The module never
moves partitions and does not modify the NTFS filesystem. Its purpose is to make
the Windows partition large enough for the backed-up partclone image.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backup_engine.backup_planner import discover_layout, parent_disk_path, read_bitlocker_state
from backup_engine.backup_state import DEFAULT_IMAGE_FILES
from common.command import CommandResult, run_command, run_readonly
from recovery_runtime.discover import require_linux

MIN_ADJACENT_FREE_BYTES = 1 * 1024 * 1024
PARTCLONE_RESTORE_SIZE_MARGIN_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class PartitionGeometry:
    node: str
    number: int
    start_sector: int
    size_sectors: int
    sector_size: int

    @property
    def end_sector(self) -> int:
        return self.start_sector + self.size_sectors - 1

    @property
    def size_bytes(self) -> int:
        return self.size_sectors * self.sector_size


@dataclass(frozen=True)
class WindowsExtendPlan:
    status: str
    can_extend: bool
    reason: Optional[str] = None
    bitlocker: str = "UNKNOWN"
    disk_path: Optional[str] = None
    windows_partition: Optional[str] = None
    partition_number: Optional[int] = None
    current_size_bytes: int = 0
    available_after_bytes: int = 0
    required_size_bytes: int = 0
    restore_margin_bytes: int = 0
    target_size_bytes: int = 0
    current_end_sector: Optional[int] = None
    target_end_sector: Optional[int] = None
    next_partition: Optional[str] = None
    planned_commands: dict[str, str] = field(default_factory=dict)
    planned_operations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _reject(
    reason: str,
    *,
    status: str = "REJECTED",
    bitlocker: str = "UNKNOWN",
    disk_path: Optional[str] = None,
    windows_partition: Optional[str] = None,
    current_size_bytes: int = 0,
    next_partition: Optional[str] = None,
) -> WindowsExtendPlan:
    return WindowsExtendPlan(
        status=status,
        can_extend=False,
        reason=reason,
        bitlocker=bitlocker,
        disk_path=disk_path,
        windows_partition=windows_partition,
        current_size_bytes=current_size_bytes,
        next_partition=next_partition,
    )


def _required_tools_missing() -> list[str]:
    required = ("partclone.info", "parted", "partprobe", "sfdisk", "blockdev")
    return [name for name in required if shutil.which(name) is None]


def _partition_number(partition_path: str) -> int:
    name = Path(partition_path).name
    match = re.search(r"p(\d+)$", name) or re.search(r"(\d+)$", name)
    if not match:
        raise RuntimeError(f"cannot determine partition number from {partition_path}")
    return int(match.group(1))


def _command_detail(result: CommandResult, label: str) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
    return f"{label} failed (exit {result.returncode}): {detail}"


def _read_partition_table(disk_path: str) -> dict[str, Any]:
    result = run_readonly(["sfdisk", "--json", disk_path])
    if result.returncode != 0:
        raise RuntimeError(_command_detail(result, "sfdisk"))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"sfdisk json parse failed: {exc}") from exc
    table = payload.get("partitiontable")
    if not isinstance(table, dict):
        raise RuntimeError("sfdisk json did not contain a partition table")
    return table


def _geometry_from_table(table: dict[str, Any], partition_path: str) -> PartitionGeometry:
    sector_size = int(table.get("sectorsize") or 512)
    requested_number = _partition_number(partition_path)
    for part in table.get("partitions") or []:
        node = str(part.get("node") or "")
        number_value = part.get("number")
        if number_value is None and node:
            number_value = _partition_number(node)
        number = int(number_value or 0)
        if node != partition_path and number != requested_number:
            continue
        return PartitionGeometry(
            node=node or partition_path,
            number=number or requested_number,
            start_sector=int(part.get("start")),
            size_sectors=int(part.get("size")),
            sector_size=sector_size,
        )
    raise RuntimeError(f"partition geometry not found for {partition_path}")


def _next_partition_after(
    table: dict[str, Any],
    geometry: PartitionGeometry,
) -> Optional[dict[str, Any]]:
    candidates = []
    for part in table.get("partitions") or []:
        node = str(part.get("node") or "")
        if node == geometry.node:
            continue
        start = int(part.get("start"))
        if start > geometry.end_sector:
            candidates.append(part)
    if not candidates:
        return None
    return min(candidates, key=lambda part: int(part.get("start")))


def _last_usable_sector(table: dict[str, Any]) -> int:
    if table.get("lastlba") is not None:
        return int(table["lastlba"])
    disk_path = str(table.get("device") or "")
    result = run_readonly(["blockdev", "--getsz", disk_path])
    if result.returncode == 0:
        return max(0, int(result.stdout.strip()) - 34)
    raise RuntimeError("unable to determine disk last usable sector")


def _block_device_size_bytes(partition_path: str) -> int:
    result = run_readonly(["blockdev", "--getsize64", partition_path])
    if result.returncode != 0:
        raise RuntimeError(_command_detail(result, "block device size probe"))
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("block device size probe did not report a byte size") from exc


def _is_partition_mounted(partition_path: str) -> bool:
    try:
        with Path("/proc/self/mounts").open("r", encoding="utf-8") as handle:
            return any(line.split()[0] == partition_path for line in handle if line.split())
    except OSError:
        return False


def _format_bytes(value: int) -> str:
    if value < 1024**2:
        return f"{value} B"
    if value < 1024**3:
        return f"{value / 1024**2:.1f} MiB"
    return f"{value / 1024**3:.2f} GiB"


def _parse_partclone_source_size(output: str) -> int:
    patterns = {
        "source_blocks": r"Device size:\s+.*=\s*(\d+)\s+Blocks",
        "block_size": r"Block size:\s*(\d+)\s+Byte",
    }
    parsed: dict[str, int] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match is None:
            raise RuntimeError(f"partclone.info output missing {key}")
        parsed[key] = int(match.group(1))
    return parsed["source_blocks"] * parsed["block_size"]


def _backup_windows_requirement_bytes(recovery_root: Optional[Path]) -> int:
    if recovery_root is None:
        raise RuntimeError("RECOVERY_IMAGE is not mounted; backup image requirement unavailable")
    image = recovery_root / DEFAULT_IMAGE_FILES["windows"]
    if not image.is_file():
        raise RuntimeError("Windows partclone image is missing; restore requirement unavailable")
    result = run_readonly(["partclone.info", "-L", "/dev/null", "-s", str(image)])
    if result.returncode != 0:
        raise RuntimeError(_command_detail(result, "partclone.info"))
    return _parse_partclone_source_size("\n".join((result.stdout, result.stderr)))


def plan_windows_partition_extend(
    *,
    recovery_root: Optional[Path] = None,
) -> WindowsExtendPlan:
    require_linux()
    bitlocker = read_bitlocker_state(live=True)
    if bitlocker == "ON":
        return _reject("BitLocker is ON; restore partition preparation refused", bitlocker=bitlocker)

    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        return _reject(topology_reason or "layout discovery failed", bitlocker=bitlocker)

    disk_path = parent_disk_path(layout.windows.path)
    missing = _required_tools_missing()
    if missing:
        return _reject(
            f"required tools missing: {', '.join(missing)}",
            bitlocker=bitlocker,
            disk_path=disk_path,
            windows_partition=layout.windows.path,
        )

    if _is_partition_mounted(layout.windows.path):
        return _reject(
            "Windows partition is mounted; extension refused",
            bitlocker=bitlocker,
            disk_path=disk_path,
            windows_partition=layout.windows.path,
        )

    try:
        required_size_bytes = _backup_windows_requirement_bytes(recovery_root)
    except RuntimeError as exc:
        return _reject(
            str(exc),
            bitlocker=bitlocker,
            disk_path=disk_path,
            windows_partition=layout.windows.path,
        )

    table = _read_partition_table(disk_path)
    geometry = _geometry_from_table(table, layout.windows.path)
    target_requirement_bytes = required_size_bytes + PARTCLONE_RESTORE_SIZE_MARGIN_BYTES

    if geometry.size_bytes >= target_requirement_bytes:
        return WindowsExtendPlan(
            status="READY",
            can_extend=False,
            reason="Windows partition is already large enough for restore.",
            bitlocker=bitlocker,
            disk_path=disk_path,
            windows_partition=layout.windows.path,
            partition_number=geometry.number,
            current_size_bytes=geometry.size_bytes,
            required_size_bytes=required_size_bytes,
            restore_margin_bytes=PARTCLONE_RESTORE_SIZE_MARGIN_BYTES,
            target_size_bytes=geometry.size_bytes,
            current_end_sector=geometry.end_sector,
            target_end_sector=geometry.end_sector,
            planned_operations=[
                "verify Windows partition is not mounted",
                "verify Windows partition size is large enough for restore",
            ],
        )

    next_partition = _next_partition_after(table, geometry)
    if next_partition is not None:
        max_target_end = int(next_partition.get("start")) - 1
        next_node = str(next_partition.get("node") or "")
    else:
        max_target_end = _last_usable_sector(table)
        next_node = None

    free_sectors = max(0, max_target_end - geometry.end_sector)
    available_after_bytes = free_sectors * geometry.sector_size
    required_sectors = math.ceil(target_requirement_bytes / geometry.sector_size)
    target_end = geometry.start_sector + required_sectors - 1
    target_size_bytes = required_sectors * geometry.sector_size

    if available_after_bytes < MIN_ADJACENT_FREE_BYTES:
        reason = "No adjacent free space was found after the Windows partition."
        if next_node:
            reason = f"{reason} Next partition: {next_node}."
        return _reject(
            reason,
            bitlocker=bitlocker,
            disk_path=disk_path,
            windows_partition=layout.windows.path,
            current_size_bytes=geometry.size_bytes,
            next_partition=next_node,
        )

    if target_end > max_target_end:
        reason = (
            "Adjacent free space is smaller than the backup image requirement. "
            f"Required: {_format_bytes(target_requirement_bytes)}."
        )
        if next_node:
            reason = f"{reason} Next partition: {next_node}."
        return _reject(
            reason,
            bitlocker=bitlocker,
            disk_path=disk_path,
            windows_partition=layout.windows.path,
            current_size_bytes=geometry.size_bytes,
            next_partition=next_node,
        )

    commands = {
        "backup_gpt": f"sfdisk --dump {disk_path}",
        "extend_partition": (
            f"parted -s {disk_path} unit s resizepart {geometry.number} {target_end}s"
        ),
        "partprobe": f"partprobe {disk_path}",
    }
    operations = [
        "verify Windows partition is not mounted",
        "read Windows backup image restore requirement",
        "verify adjacent free space can satisfy restore requirement",
        "save GPT backup before changing the partition boundary",
        "extend Windows partition boundary only",
        "refresh kernel partition table",
        "verify Windows partition boundary is large enough for restore",
    ]
    return WindowsExtendPlan(
        status="PLANNED",
        can_extend=True,
        reason=None,
        bitlocker=bitlocker,
        disk_path=disk_path,
        windows_partition=layout.windows.path,
        partition_number=geometry.number,
        current_size_bytes=geometry.size_bytes,
        available_after_bytes=available_after_bytes,
        required_size_bytes=required_size_bytes,
        restore_margin_bytes=PARTCLONE_RESTORE_SIZE_MARGIN_BYTES,
        target_size_bytes=target_size_bytes,
        current_end_sector=geometry.end_sector,
        target_end_sector=target_end,
        next_partition=next_node,
        planned_commands=commands,
        planned_operations=operations,
    )


def _run_step(argv: list[str], *, label: str, operations: list[str]) -> None:
    result = run_command(argv, dry_run=False, confirmed=True)
    if result.returncode != 0:
        raise RuntimeError(_command_detail(result, label))
    operations.append(f"{label}: {' '.join(argv)}")


def _settle_disk(disk_path: str, operations: list[str]) -> None:
    for argv, label in (
        (["partprobe", disk_path], "partprobe"),
        (["blockdev", "--rereadpt", disk_path], "blockdev rereadpt"),
    ):
        result = run_command(argv, dry_run=False, confirmed=True)
        if result.returncode == 0:
            operations.append(f"{label}: {' '.join(argv)}")
    if shutil.which("udevadm"):
        result = run_command(["udevadm", "settle"], dry_run=False, confirmed=True)
        if result.returncode == 0:
            operations.append("udevadm settle")


def _write_gpt_backup(
    disk_path: str,
    backup_root: Optional[Path],
    operations: list[str],
) -> Optional[str]:
    result = run_readonly(["sfdisk", "--dump", disk_path])
    if result.returncode != 0:
        raise RuntimeError(_command_detail(result, "GPT backup"))
    if backup_root is None:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = backup_root / "metadata"
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"gpt_before_windows_extend_{stamp}.sfdisk"
    path.write_text(result.stdout, encoding="utf-8")
    operations.append(f"GPT backup saved: {path}")
    return str(path)


def _write_plan_snapshot(
    plan: WindowsExtendPlan,
    backup_root: Optional[Path],
    operations: list[str],
) -> Optional[str]:
    if backup_root is None:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    state_dir = backup_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"windows_extend_plan_{stamp}.json"
    path.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    operations.append(f"extend plan saved: {path}")
    return str(path)


def run_windows_partition_extend(
    *,
    backup_root: Optional[Path] = None,
) -> dict[str, Any]:
    require_linux()
    plan = plan_windows_partition_extend(recovery_root=backup_root)
    operations = list(plan.planned_operations)
    if not plan.can_extend:
        return {
            "status": plan.status,
            "reason": plan.reason,
            "plan": plan.to_dict(),
            "planned_operations": operations,
        }

    try:
        plan_snapshot = _write_plan_snapshot(plan, backup_root, operations)
        gpt_backup = _write_gpt_backup(str(plan.disk_path), backup_root, operations)
        _run_step(
            [
                "parted",
                "-s",
                str(plan.disk_path),
                "unit",
                "s",
                "resizepart",
                str(plan.partition_number),
                f"{plan.target_end_sector}s",
            ],
            label="extend Windows partition boundary",
            operations=operations,
        )
        _settle_disk(str(plan.disk_path), operations)
        detected_size = _block_device_size_bytes(str(plan.windows_partition))
        operations.append(
            "post-resize partition size detected: "
            f"{detected_size} bytes expected at least {plan.target_size_bytes} bytes"
        )
        if detected_size < int(plan.target_size_bytes):
            return {
                "status": "PENDING_REBOOT",
                "reason": (
                    "Windows partition boundary was requested, but the system has not "
                    "detected the new partition size yet."
                ),
                "next_step": (
                    "Restart this computer, then run Restore Partition Preparation again "
                    "from Administrator Mode."
                ),
                "plan": plan.to_dict(),
                "gpt_backup": gpt_backup,
                "plan_snapshot": plan_snapshot,
                "planned_operations": operations,
            }
        return {
            "status": "COMPLETED",
            "message": "Windows partition is now large enough for restore.",
            "plan": plan.to_dict(),
            "gpt_backup": gpt_backup,
            "plan_snapshot": plan_snapshot,
            "planned_operations": operations,
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason": str(exc),
            "plan": plan.to_dict(),
            "planned_operations": operations,
        }

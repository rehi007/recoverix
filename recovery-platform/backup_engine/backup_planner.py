"""Dry-run backup plan generation only (no mount/partclone/execution)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from recovery_runtime.discover import DiscoveredVolume

from backup_engine.backup_plan import BackupCommands, BackupPlan, SourceInfo, TargetInfo
from backup_engine.partclone_wrapper import (
    format_gpt_backup_command,
    format_partclone_fat_command,
    format_partclone_ntfs_command,
    format_sha256_command,
)
from common.logger import get_logger, setup_logging
from partition_manager.models import LABEL_RECOVERY_IMAGE, LABEL_RECOVERY_LINUX
from backup_engine.backup_state import DEFAULT_IMAGE_FILES, has_incomplete_backup
from backup_engine.manifest import MANIFEST_FILENAME, load_recovery_manifest
from recovery_runtime.integrity import find_manifest

logger = get_logger(__name__)

IMAGE_RELATIVE = Path("images/windows_backup.pcl")
EFI_IMAGE_RELATIVE = Path("images/efi_backup.pcl")
GPT_METADATA_RELATIVE = Path("metadata/gpt_backup.bin")
MANIFEST_RELATIVE = Path("recovery/manifest.json")
DEFAULT_EXPECTED_MOUNT = "/mnt/recovery"

_APPLY_FORBIDDEN_MSG = (
    "backup_planner does not execute backup. Use backup_engine.run_backup in step 11."
)


@dataclass
class _DiscoveredLayout:
    windows: "DiscoveredVolume"
    efi: "DiscoveredVolume"
    recovery_image: "DiscoveredVolume"
    disk_path: str
    volumes: List["DiscoveredVolume"]


def read_bitlocker_state(*, live: bool) -> str:
    if not live or sys.platform != "win32":
        return "UNKNOWN"
    from partition_manager.provisioning_planner import read_bitlocker_state as win_bl

    return win_bl(live=True)


def _recovery_labels() -> set[str]:
    return {LABEL_RECOVERY_IMAGE, LABEL_RECOVERY_LINUX}


def parent_disk_path(partition_path: str) -> str:
    """Derive parent disk device path from a partition path."""
    name = Path(partition_path).name
    nvme = re.match(r"^(nvme\d+n\d+)p\d+$", name)
    if nvme:
        return f"/dev/{nvme.group(1)}"
    scsi = re.match(r"^([a-z]+)\d+$", name)
    if scsi:
        return f"/dev/{scsi.group(1)}"
    return partition_path


def find_efi_partitions(volumes: List["DiscoveredVolume"]) -> List["DiscoveredVolume"]:
    fat_types = {"vfat", "fat", "fat32"}
    return [
        vol
        for vol in volumes
        if vol.device_type == "part"
        and (vol.fstype or "").lower() in fat_types
        and vol.label not in _recovery_labels()
    ]


def find_windows_partitions(volumes: List["DiscoveredVolume"]) -> List["DiscoveredVolume"]:
    return [
        vol
        for vol in volumes
        if vol.device_type == "part"
        and (vol.fstype or "").lower() == "ntfs"
        and vol.label not in _recovery_labels()
    ]


def validate_topology(
    *,
    windows_candidates: List["DiscoveredVolume"],
    efi_candidates: List["DiscoveredVolume"],
    recovery: Optional["DiscoveredVolume"],
) -> Tuple[Optional[str], Optional[_DiscoveredLayout]]:
    """Validate single boot disk topology; return rejection reason or layout."""
    if recovery is None:
        return "RECOVERY_IMAGE partition not found", None
    if not windows_candidates:
        return "Windows OS partition not found", None
    if len(windows_candidates) > 1:
        return "unsupported disk topology: multiple Windows OS partitions", None
    if not efi_candidates:
        return "EFI System Partition not found", None
    if len(efi_candidates) > 1:
        return "unsupported disk topology: multiple EFI System Partitions", None

    windows = windows_candidates[0]
    efi = efi_candidates[0]
    disk = parent_disk_path(windows.path)
    efi_disk = parent_disk_path(efi.path)
    recovery_disk = parent_disk_path(recovery.path)

    disks = {disk, efi_disk, recovery_disk}
    if len(disks) > 1:
        return (
            "unsupported disk topology: EFI, Windows, and Recovery Image are on different disks",
            None,
        )

    return None, _DiscoveredLayout(
        windows=windows,
        efi=efi,
        recovery_image=recovery,
        disk_path=disk,
        volumes=[],
    )


def discover_layout() -> Tuple[Optional[str], Optional[_DiscoveredLayout]]:
    """Discover backup layout; return (rejection_reason, layout)."""
    from recovery_runtime.discover import discover_volumes, find_by_label, require_linux

    require_linux()
    volumes = discover_volumes()
    recovery = find_by_label(volumes, LABEL_RECOVERY_IMAGE)
    windows_candidates = find_windows_partitions(volumes)
    efi_candidates = find_efi_partitions(volumes)
    reason, layout = validate_topology(
        windows_candidates=windows_candidates,
        efi_candidates=efi_candidates,
        recovery=recovery,
    )
    if layout is not None:
        layout = _DiscoveredLayout(
            windows=layout.windows,
            efi=layout.efi,
            recovery_image=layout.recovery_image,
            disk_path=layout.disk_path,
            volumes=volumes,
        )
    return reason, layout


def has_valid_backup(recovery_root: Path) -> bool:
    # Fail-closed when an incomplete backup marker exists.
    if has_incomplete_backup(recovery_root):
        return False

    # Prefer our canonical manifest layout, but tolerate legacy locations.
    # `load_recovery_manifest()` will be updated to search metadata/ too.
    try:
        manifest = load_recovery_manifest(recovery_root)
    except FileNotFoundError:
        # Fallback for very old layouts (best-effort; not required for canonical runtime).
        manifest_path = find_manifest(recovery_root) or recovery_root / MANIFEST_RELATIVE
        if not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False

    if not manifest.get("backup_complete"):
        return False
    if not manifest.get("sha256_hashes"):
        return False

    # Ensure core artifacts exist and are non-empty.
    required_relatives = [
        DEFAULT_IMAGE_FILES["windows"],
        DEFAULT_IMAGE_FILES["efi"],
        DEFAULT_IMAGE_FILES["gpt"],
    ]
    for rel in required_relatives:
        p = recovery_root / rel
        if not p.is_file() or p.stat().st_size <= 0:
            return False

    manifest_ok = (
        (recovery_root / "manifests" / "recovery-manifest.json").is_file()
        or (recovery_root / "metadata" / "recovery-manifest.json").is_file()
        or (recovery_root / "recovery-manifest.json").is_file()
    )
    if not manifest_ok:
        return False

    # UI/restore validation also expects sidecar SHA256 files.
    hashes_dir = recovery_root / "hashes"
    windows_stem = Path(DEFAULT_IMAGE_FILES["windows"]).stem
    efi_stem = Path(DEFAULT_IMAGE_FILES["efi"]).stem
    sidecars = [
        hashes_dir / f"{windows_stem}.sha256",
        hashes_dir / f"{efi_stem}.sha256",
        hashes_dir / f"{Path(DEFAULT_IMAGE_FILES['gpt']).stem}.sha256",
        hashes_dir / "manifest.sha256",
    ]
    if not all(p.is_file() for p in sidecars):
        return False

    return True


def _planned_steps() -> List[str]:
    return [
        "backup GPT metadata",
        "backup EFI System Partition",
        "backup Windows OS Partition using partclone.ntfs",
        "generate SHA256 hashes",
        "generate recovery manifest",
        "validate generated backup",
    ]


def _build_manifest_plan(
    *,
    layout: _DiscoveredLayout,
    recovery_mount: str,
) -> dict:
    return {
        "version": 1,
        "valid": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "disk": layout.disk_path,
        "efi_partition": layout.efi.path,
        "windows_partition": layout.windows.path,
        "recovery_image_partition": layout.recovery_image.path,
        "recovery_mount": recovery_mount,
        "artifacts": {
            "gpt": str(GPT_METADATA_RELATIVE).replace("\\", "/"),
            "efi": str(EFI_IMAGE_RELATIVE).replace("\\", "/"),
            "windows": str(IMAGE_RELATIVE).replace("\\", "/"),
        },
        "sha256": {},
        "notes": "manifest populated after step 11 backup execution",
    }


def build_backup_plan(
    *,
    layout: Optional[_DiscoveredLayout],
    bitlocker: str,
    topology_reason: Optional[str] = None,
) -> BackupPlan:
    """Create a dry-run backup plan (never executes commands)."""
    planned_steps = _planned_steps()

    if bitlocker == "ON":
        return BackupPlan(
            status="REJECTED",
            can_backup=False,
            dry_run=True,
            execution_allowed=False,
            reason="BitLocker is ON; backup refused",
            bitlocker=bitlocker,
            planned_steps=planned_steps,
        )

    if topology_reason:
        return BackupPlan(
            status="REJECTED",
            can_backup=False,
            dry_run=True,
            execution_allowed=False,
            reason=topology_reason,
            bitlocker=bitlocker,
            planned_steps=planned_steps,
        )

    if layout is None:
        return BackupPlan(
            status="REJECTED",
            can_backup=False,
            dry_run=True,
            execution_allowed=False,
            reason="required partitions not found",
            bitlocker=bitlocker,
            planned_steps=planned_steps,
        )

    recovery_mount = layout.recovery_image.mountpoint or DEFAULT_EXPECTED_MOUNT
    mount_required = layout.recovery_image.mountpoint is None
    recovery_accessible = layout.recovery_image.mountpoint is not None

    source = SourceInfo(
        efi_partition=layout.efi.path,
        windows_partition=layout.windows.path,
        disk=layout.disk_path,
    )
    targets = TargetInfo(
        recovery_image_partition=layout.recovery_image.path,
        mount_required=mount_required,
        expected_mount_point=recovery_mount,
        recovery_accessible=recovery_accessible,
    )

    if recovery_accessible and has_valid_backup(Path(recovery_mount)):
        return BackupPlan(
            status="BLOCKED",
            can_backup=False,
            dry_run=True,
            execution_allowed=False,
            reason="valid backup already exists; new backup plan blocked",
            bitlocker=bitlocker,
            source=source,
            targets=targets,
            planned_steps=planned_steps,
        )

    recovery_root = Path(recovery_mount)
    gpt_out = recovery_root / GPT_METADATA_RELATIVE
    efi_out = recovery_root / EFI_IMAGE_RELATIVE
    windows_out = recovery_root / IMAGE_RELATIVE

    commands = BackupCommands(
        gpt_backup=format_gpt_backup_command(layout.disk_path, gpt_out),
        efi_backup=format_partclone_fat_command(layout.efi.path, efi_out),
        windows_partclone=format_partclone_ntfs_command(layout.windows.path, windows_out),
    )
    sha256_plan = [
        format_sha256_command(gpt_out),
        format_sha256_command(efi_out),
        format_sha256_command(windows_out),
    ]
    manifest_plan = _build_manifest_plan(layout=layout, recovery_mount=recovery_mount)

    if mount_required:
        return BackupPlan(
            status="MOUNT_REQUIRED",
            can_backup=True,
            dry_run=True,
            execution_allowed=False,
            reason="backup execution requires mounted recovery image",
            bitlocker=bitlocker,
            source=source,
            targets=targets,
            planned_steps=planned_steps,
            commands=commands,
            manifest_plan=manifest_plan,
            sha256_plan=sha256_plan,
        )

    return BackupPlan(
        status="PLANNED",
        can_backup=True,
        dry_run=True,
        execution_allowed=False,
        reason=None,
        bitlocker=bitlocker,
        source=source,
        targets=targets,
        planned_steps=planned_steps,
        commands=commands,
        manifest_plan=manifest_plan,
        sha256_plan=sha256_plan,
    )


def create_backup_plan(*, live: bool = True) -> BackupPlan:
    """Discover environment and build a dry-run backup plan."""
    from recovery_runtime.discover import require_linux

    require_linux()
    bitlocker = read_bitlocker_state(live=live)
    topology_reason, layout = discover_layout()
    return build_backup_plan(
        layout=layout,
        bitlocker=bitlocker,
        topology_reason=topology_reason,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Generate backup plan only (no execution)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate dry-run backup plan (required)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--apply",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.apply:
        print(_APPLY_FORBIDDEN_MSG, file=sys.stderr)
        return 2

    if not args.dry_run:
        parser.error("--dry-run is required for backup_planner")

    plan = create_backup_plan()
    print(plan.to_json() if args.json or not args.json else plan.to_json())
    return 0 if plan.status in ("PLANNED", "MOUNT_REQUIRED") else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""EFI System Partition backup helpers (reliability + fail-closed verification)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from backup_engine.backup_planner import EFI_IMAGE_RELATIVE
from backup_engine.backup_runtime_log import runtime_log
from backup_engine.backup_state import DEFAULT_IMAGE_FILES
from backup_engine.partclone_wrapper import format_partclone_fat_command
from common.command import run_readonly


class EfiBackupError(RuntimeError):
    """EFI backup step failed; incomplete marker must remain."""


def efi_image_output_path(recovery_root: Path) -> Path:
    return recovery_root / EFI_IMAGE_RELATIVE


def _probe_fstype(device: str) -> Optional[str]:
    result = run_readonly(["blkid", "-o", "value", "-s", "TYPE", device])
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def verify_efi_source(efi_partition: str) -> None:
    """Verify EFI partition device exists and looks like a FAT ESP."""
    dev = Path(efi_partition)
    if not dev.exists():
        raise EfiBackupError(f"EFI partition device not found: {efi_partition}")

    fstype = (_probe_fstype(efi_partition) or "").lower()
    if fstype and fstype not in ("vfat", "fat", "fat32", "msdos"):
        raise EfiBackupError(
            f"EFI partition {efi_partition} has unexpected filesystem TYPE={fstype}"
        )
    runtime_log(f"EFI source detection: device={efi_partition} fstype={fstype or 'unknown'}")


def prepare_efi_backup_output(recovery_root: Path) -> Path:
    """
    Prepare canonical EFI output path under images/efi_backup.pcl.

    Removes legacy mistaken efi_backup/ directory at recovery root when empty.
    Fails closed when legacy dir exists with content but canonical image missing.
    """
    output = efi_image_output_path(recovery_root)
    legacy_dir = recovery_root / "efi_backup"

    if legacy_dir.is_dir() and not output.is_file():
        try:
            has_entries = any(legacy_dir.iterdir())
        except OSError as exc:
            raise EfiBackupError(f"cannot inspect legacy efi_backup/ directory: {exc}") from exc
        if has_entries:
            raise EfiBackupError(
                "legacy efi_backup/ directory exists without images/efi_backup.pcl; "
                "remove or re-run full backup"
            )
        shutil.rmtree(legacy_dir, ignore_errors=True)
        runtime_log("EFI prepare: removed empty legacy efi_backup/ directory")

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.is_dir():
        raise EfiBackupError(
            f"EFI output path is a directory, expected file: {output.relative_to(recovery_root)}"
        )
    return output


def verify_efi_artifact(recovery_root: Path, *, min_size: int = 512) -> None:
    """Verify images/efi_backup.pcl exists and is non-trivial after partclone."""
    output = efi_image_output_path(recovery_root)
    if not output.is_file():
        raise EfiBackupError(
            f"EFI backup artifact missing: {DEFAULT_IMAGE_FILES['efi']}"
        )
    size = output.stat().st_size
    if size < min_size:
        raise EfiBackupError(
            f"EFI backup artifact too small ({size} bytes): {DEFAULT_IMAGE_FILES['efi']}"
        )
    runtime_log(f"EFI artifact verified: {DEFAULT_IMAGE_FILES['efi']} size={size}")


def format_efi_backup_command(efi_partition: str, recovery_root: Path) -> str:
    output = prepare_efi_backup_output(recovery_root)
    return format_partclone_fat_command(efi_partition, output)


def _log_efi_mount_state(efi_partition: str) -> None:
    """Log whether ESP is already mounted (partclone uses the block device either way)."""
    result = run_readonly(["findmnt", "-n", "-o", "TARGET", efi_partition])
    if result.returncode == 0 and (result.stdout or "").strip():
        runtime_log(f"EFI mount: {result.stdout.strip()} (already mounted)")
    else:
        runtime_log(f"EFI mount: not mounted; partclone reads block device {efi_partition}")


def run_efi_backup_precheck(efi_partition: str, recovery_root: Path) -> Path:
    """Run EFI prechecks and return canonical output path."""
    runtime_log("EFI backup start")
    _log_efi_mount_state(efi_partition)
    verify_efi_source(efi_partition)
    output = prepare_efi_backup_output(recovery_root)
    runtime_log(f"EFI backup target: {output.relative_to(recovery_root)}")
    return output

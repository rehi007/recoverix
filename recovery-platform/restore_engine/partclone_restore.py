"""Restore command string builders only (no execution)."""

from __future__ import annotations

from pathlib import Path

PARTCLONE_NTFS = "partclone.ntfs"
PARTCLONE_FAT = "partclone.fat"

BOOTMGFW_EFI_PATH = "EFI/Microsoft/Boot/bootmgfw.efi"


def format_partclone_ntfs_restore_command(image: Path, target_partition: str) -> str:
    """Return partclone.ntfs restore command string (not executed)."""
    return f"{PARTCLONE_NTFS} -r -s {image} -o {target_partition}"


def format_partclone_fat_restore_command(image: Path, target_partition: str) -> str:
    """Return partclone.fat restore command string for EFI (not executed)."""
    return f"{PARTCLONE_FAT} -r -s {image} -o {target_partition}"


def format_gpt_load_command(backup_file: Path, disk_path: str) -> str:
    """Return GPT metadata restore command string (sgdisk --load-backup)."""
    return f"sgdisk --load-backup {backup_file} {disk_path}"


def format_gpt_snapshot_command(output_file: Path, disk_path: str) -> str:
    """Return pre-restore GPT snapshot command string (sgdisk --backup)."""
    return f"sgdisk --backup {output_file} {disk_path}"

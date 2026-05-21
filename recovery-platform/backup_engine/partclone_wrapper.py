"""partclone and related backup command string builders (no execution)."""

from __future__ import annotations

from pathlib import Path

PARTCLONE_NTFS = "partclone.ntfs"
PARTCLONE_FAT = "partclone.fat"


def format_partclone_ntfs_command(source_partition: str, output_image: Path) -> str:
    """Return partclone.ntfs backup command string."""
    return (
        f"{PARTCLONE_NTFS} -c -s {source_partition} -o {output_image}"
    )


def format_partclone_fat_command(source_partition: str, output_image: Path) -> str:
    """Return partclone.fat backup command string for EFI System Partition."""
    return (
        f"{PARTCLONE_FAT} -c -s {source_partition} -o {output_image}"
    )


def format_gpt_backup_command(disk_path: str, output_file: Path) -> str:
    """Return GPT metadata backup command string (sgdisk)."""
    return f"sgdisk --backup {output_file} {disk_path}"


def format_sha256_command(file_path: Path) -> str:
    """Return SHA256 checksum command string."""
    return f"sha256sum {file_path}"


def run_partclone_backup(*_args, **_kwargs):
    """
    Execution is not available in step 9.

    Actual backup runs in backup_engine.run_backup (step 11).
    """
    raise NotImplementedError(
        "backup_planner does not execute backup. Use backup_engine.run_backup in step 11."
    )

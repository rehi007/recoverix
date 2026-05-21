"""Image backup via partclone and related tooling."""

from .backup_plan import BackupCommands, BackupPlan, SourceInfo, TargetInfo
from .backup_planner import create_backup_plan
from . import run_backup as run_backup_module
from .backup_state import mark_incomplete_backup
from .hash import sha256_file, sha256_files
from .manifest import (
    DiskMetadata,
    HashGenerationError,
    ManifestCreationError,
    create_recovery_manifest,
    finalize_backup_manifest,
    load_recovery_manifest,
    write_recovery_manifest,
)
from .partclone_wrapper import (
    format_gpt_backup_command,
    format_partclone_fat_command,
    format_partclone_ntfs_command,
)

__all__ = [
    "BackupCommands",
    "BackupPlan",
    "DiskMetadata",
    "SourceInfo",
    "TargetInfo",
    "HashGenerationError",
    "ManifestCreationError",
    "create_backup_plan",
    "create_recovery_manifest",
    "finalize_backup_manifest",
    "mark_incomplete_backup",
    "run_backup_module",
    "format_gpt_backup_command",
    "format_partclone_fat_command",
    "format_partclone_ntfs_command",
    "load_recovery_manifest",
    "sha256_file",
    "sha256_files",
    "write_recovery_manifest",
]

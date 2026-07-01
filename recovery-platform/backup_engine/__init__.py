"""Image backup package.

Keep package initialization lightweight. Windows-side tooling imports selected
backup_engine submodules for constants, and must not load runtime-only backup
execution dependencies as a side effect.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BackupCommands",
    "BackupPlan",
    "DiskMetadata",
    "HashGenerationError",
    "ManifestCreationError",
    "SourceInfo",
    "TargetInfo",
    "create_backup_plan",
    "create_recovery_manifest",
    "finalize_backup_manifest",
    "format_gpt_backup_command",
    "format_partclone_fat_command",
    "format_partclone_ntfs_command",
    "format_partclone_ntfs_domain_command",
    "load_recovery_manifest",
    "mark_incomplete_backup",
    "run_backup_module",
    "sha256_file",
    "sha256_files",
    "write_recovery_manifest",
]


def __getattr__(name: str) -> Any:
    if name in {"BackupCommands", "BackupPlan", "SourceInfo", "TargetInfo"}:
        from .backup_plan import BackupCommands, BackupPlan, SourceInfo, TargetInfo

        return {
            "BackupCommands": BackupCommands,
            "BackupPlan": BackupPlan,
            "SourceInfo": SourceInfo,
            "TargetInfo": TargetInfo,
        }[name]
    if name == "create_backup_plan":
        from .backup_planner import create_backup_plan

        return create_backup_plan
    if name == "mark_incomplete_backup":
        from .backup_state import mark_incomplete_backup

        return mark_incomplete_backup
    if name in {
        "DiskMetadata",
        "HashGenerationError",
        "ManifestCreationError",
        "create_recovery_manifest",
        "finalize_backup_manifest",
        "load_recovery_manifest",
        "write_recovery_manifest",
    }:
        from .manifest import (
            DiskMetadata,
            HashGenerationError,
            ManifestCreationError,
            create_recovery_manifest,
            finalize_backup_manifest,
            load_recovery_manifest,
            write_recovery_manifest,
        )

        return {
            "DiskMetadata": DiskMetadata,
            "HashGenerationError": HashGenerationError,
            "ManifestCreationError": ManifestCreationError,
            "create_recovery_manifest": create_recovery_manifest,
            "finalize_backup_manifest": finalize_backup_manifest,
            "load_recovery_manifest": load_recovery_manifest,
            "write_recovery_manifest": write_recovery_manifest,
        }[name]
    if name in {
        "format_gpt_backup_command",
        "format_partclone_fat_command",
        "format_partclone_ntfs_command",
        "format_partclone_ntfs_domain_command",
    }:
        from .partclone_wrapper import (
            format_gpt_backup_command,
            format_partclone_fat_command,
            format_partclone_ntfs_command,
            format_partclone_ntfs_domain_command,
        )

        return {
            "format_gpt_backup_command": format_gpt_backup_command,
            "format_partclone_fat_command": format_partclone_fat_command,
            "format_partclone_ntfs_command": format_partclone_ntfs_command,
            "format_partclone_ntfs_domain_command": format_partclone_ntfs_domain_command,
        }[name]
    if name == "run_backup_module":
        from . import run_backup as run_backup_module

        return run_backup_module
    if name in {"sha256_file", "sha256_files"}:
        from .hash import sha256_file, sha256_files

        return {"sha256_file": sha256_file, "sha256_files": sha256_files}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

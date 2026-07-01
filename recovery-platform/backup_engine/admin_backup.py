"""Backup type helpers for retired administrator compact backups.

Administrator compact backup previously resized the Windows partition during
backup. That workflow was removed from the commercial product path because it
can leave NTFS and partition metadata in an unsafe state after failure. This
module keeps only the compatibility helpers needed to identify legacy compact
backup manifests and label them in the runtime UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from backup_engine.backup_planner import has_valid_backup
from backup_engine.manifest import load_recovery_manifest

ADMIN_BACKUP_TYPE = "admin_compact"
STANDARD_BACKUP_TYPE = "standard"
ADMIN_BACKUP_TYPES = {
    "admin",
    "admin_compact",
    "compact",
    "compact_admin",
    "compact_backup",
}


def normalize_backup_type(value: Any) -> str:
    normalized = str(value or STANDARD_BACKUP_TYPE).strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    if normalized in ADMIN_BACKUP_TYPES:
        return ADMIN_BACKUP_TYPE
    return STANDARD_BACKUP_TYPE


def backup_type_label(value: Any) -> str:
    if normalize_backup_type(value) == ADMIN_BACKUP_TYPE:
        return "compact backup"
    return "standard backup"


def backup_type_from_manifest(manifest: dict[str, Any]) -> str:
    return normalize_backup_type(manifest.get("backup_type") or manifest.get("backup_mode"))


def current_backup_type(recovery_root: Optional[Path]) -> Optional[str]:
    if recovery_root is None:
        return None
    try:
        if not has_valid_backup(recovery_root):
            return None
        manifest = load_recovery_manifest(recovery_root)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return backup_type_from_manifest(manifest)

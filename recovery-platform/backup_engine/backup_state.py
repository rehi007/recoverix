"""Backup completion / incomplete state markers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from common.logger import get_logger

logger = get_logger(__name__)

INCOMPLETE_MARKER_RELATIVE = Path("state/incomplete_backup")

DEFAULT_IMAGE_FILES = {
    "gpt": "metadata/gpt_backup.bin",
    "efi": "images/efi.pcl",
    "windows": "images/system.pcl",
}


class BackupStateError(Exception):
    """Base error for backup state transitions."""


def incomplete_marker_path(recovery_root: Path) -> Path:
    return recovery_root / INCOMPLETE_MARKER_RELATIVE


def has_incomplete_backup(recovery_root: Path) -> bool:
    return incomplete_marker_path(recovery_root).is_file()


def mark_incomplete_backup(
    recovery_root: Path,
    reason: str,
    *,
    details: Optional[Dict[str, Any]] = None,
) -> Path:
    """Record incomplete backup state; restore must be forbidden."""
    marker = incomplete_marker_path(recovery_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "incomplete",
        "reason": reason,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "details": details or {},
    }
    marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.warning("marked incomplete backup: %s (%s)", marker, reason)
    return marker


def clear_incomplete_backup(recovery_root: Path) -> None:
    marker = incomplete_marker_path(recovery_root)
    if marker.is_file():
        marker.unlink()
        logger.info("cleared incomplete backup marker: %s", marker)


def list_missing_artifacts(
    recovery_root: Path,
    relative_paths: Iterable[str],
) -> List[str]:
    missing: List[str] = []
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if not (recovery_root / normalized).is_file():
            missing.append(normalized)
    return missing


def artifacts_complete(
    recovery_root: Path,
    relative_paths: Iterable[str],
) -> bool:
    return not list_missing_artifacts(recovery_root, relative_paths)


def is_partial_backup(
    recovery_root: Path,
    relative_paths: Iterable[str],
    *,
    manifest_exists: bool,
) -> bool:
    """
    Detect partial backup: incomplete marker or artifacts without finalized manifest.
    """
    if has_incomplete_backup(recovery_root):
        return True
    any_artifact = any(
        (recovery_root / rel.replace("\\", "/")).exists() for rel in relative_paths
    )
    return any_artifact and not manifest_exists

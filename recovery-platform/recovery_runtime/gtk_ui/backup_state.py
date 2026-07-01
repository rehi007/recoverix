"""Mock Recovery Image backup state (no partclone validation yet)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Mock marker: file presence means "valid backup exists" inside RECOVERY_IMAGE.
DEFAULT_VALID_BACKUP_MARKER = Path("/tmp/recoverix-valid-backup")


def valid_backup_exists(marker: Path | None = None) -> bool:
    """Return True when the mock valid-backup marker file exists."""
    path = marker if marker is not None else DEFAULT_VALID_BACKUP_MARKER
    return path.is_file()


@dataclass(frozen=True)
class RecoveryButtonState:
    """GTK button enable/visibility derived from mock backup + admin mode."""

    backup_sensitive: bool
    restore_sensitive: bool
    delete_visible: bool

    @classmethod
    def from_flags(cls, *, valid_backup: bool, admin_mode: bool) -> RecoveryButtonState:
        return cls(
            backup_sensitive=not valid_backup,
            restore_sensitive=valid_backup,
            delete_visible=admin_mode,
        )


def compute_button_state(
    *,
    valid_backup: bool,
    admin_mode: bool,
) -> RecoveryButtonState:
    """Policy: backup enabled only without valid backup; restore only with it."""
    return RecoveryButtonState.from_flags(valid_backup=valid_backup, admin_mode=admin_mode)

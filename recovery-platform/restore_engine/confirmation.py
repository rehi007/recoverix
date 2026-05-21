"""Interactive restore confirmation (phrase + target disk disclosure)."""

from __future__ import annotations

import sys
from typing import Callable, List, Optional, TextIO

from backup_engine.manifest import DiskMetadata, compute_device_id
from common.errors import InvalidConfirmationPhraseError

RESTORE_CONFIRMATION_PHRASE = "RESTORE THIS DEVICE"


def format_target_disk_display(disk: DiskMetadata, *, manifest_device_id: Optional[str] = None) -> List[str]:
    """Return human-readable target disk lines for operator review."""
    lines = [
        "=== RESTORE TARGET DISK ===",
        f"disk_guid   : {disk.disk_guid}",
        f"disk_serial : {disk.disk_serial}",
        f"disk_model  : {disk.disk_model}",
        f"device_id   : {compute_device_id(disk)}",
    ]
    if manifest_device_id is not None:
        lines.append(f"manifest device_id : {manifest_device_id}")
        match = manifest_device_id == compute_device_id(disk)
        lines.append(f"device_id match     : {match}")
    lines.append("===========================")
    return lines


def print_target_disk_display(
    disk: DiskMetadata,
    *,
    manifest_device_id: Optional[str] = None,
    stream: TextIO | None = None,
) -> None:
    """Print target disk identifiers before confirmation."""
    out = stream or sys.stdout
    for line in format_target_disk_display(disk, manifest_device_id=manifest_device_id):
        print(line, file=out)


def verify_confirmation_phrase(phrase: Optional[str]) -> bool:
    """Return True when phrase exactly matches the required confirmation text."""
    if phrase is None:
        return False
    return phrase.strip() == RESTORE_CONFIRMATION_PHRASE


def require_confirmation_phrase(
    phrase: Optional[str],
    *,
    disk: DiskMetadata,
    manifest_device_id: Optional[str] = None,
) -> None:
    """Raise when the operator confirmation phrase is missing or incorrect."""
    if verify_confirmation_phrase(phrase):
        return
    print_target_disk_display(disk, manifest_device_id=manifest_device_id, stream=sys.stderr)
    raise InvalidConfirmationPhraseError(
        f'confirmation phrase must be exactly: "{RESTORE_CONFIRMATION_PHRASE}"'
    )


def prompt_confirmation_phrase(
    disk: DiskMetadata,
    *,
    manifest_device_id: Optional[str] = None,
    input_func: Callable[[str], str] = input,
    stream: TextIO | None = None,
) -> str:
    """
    Display target disk info and read the confirmation phrase from the operator.

    Returns the entered phrase (stripped). Caller should pass it to require_confirmation_phrase.
    """
    out = stream or sys.stdout
    print_target_disk_display(disk, manifest_device_id=manifest_device_id, stream=out)
    print(
        f'\nType the confirmation phrase to authorize restore:\n  "{RESTORE_CONFIRMATION_PHRASE}"\n',
        file=out,
    )
    return input_func("confirmation phrase> ").strip()

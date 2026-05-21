"""Partition discovery data models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


# Well-known GPT type GUIDs (standard identifiers, not device numbers).
GPT_EFI_SYSTEM = "{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}"
GPT_MICROSOFT_RESERVED = "{e3c9e316-0b5c-4db8-817d-f92f00215ae}"

LABEL_RECOVERY_IMAGE = "RECOVERY_IMAGE"
LABEL_RECOVERY_LINUX = "RECOVERY_LINUX"

EFI_BOOT_RELATIVE = r"EFI\Microsoft\Boot\bootmgfw.efi"
WINDOWS_SYSTEM_HIVE = r"Windows\System32\config\SYSTEM"


class PartitionState(str, Enum):
    FOUND = "found"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PartitionRecord:
    """A single discovered partition role assignment."""

    disk_number: int
    partition_number: int
    role: str
    state: str
    gpt_type: Optional[str] = None
    file_system: Optional[str] = None
    label: Optional[str] = None
    drive_letter: Optional[str] = None
    size_bytes: Optional[int] = None
    access_paths: tuple[str, ...] = field(default_factory=tuple)
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["access_paths"] = list(self.access_paths)
        return payload


@dataclass(frozen=True)
class DiscoveryResult:
    """Aggregated partition discovery output."""

    efi_partition: Optional[PartitionRecord]
    msr_partition: Optional[PartitionRecord]
    windows_partition: Optional[PartitionRecord]
    recovery_image_partition: Optional[PartitionRecord]
    recovery_linux_partition: Optional[PartitionRecord]
    status: str
    dry_run: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "efi_partition": _record_or_null(self.efi_partition),
            "msr_partition": _record_or_null(self.msr_partition),
            "windows_partition": _record_or_null(self.windows_partition),
            "recovery_image_partition": _optional_record(self.recovery_image_partition),
            "recovery_linux_partition": _optional_record(self.recovery_linux_partition),
            "status": self.status,
            "dry_run": self.dry_run,
        }

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _record_or_null(record: Optional[PartitionRecord]) -> Optional[Dict[str, Any]]:
    if record is None:
        return None
    return record.to_dict()


def _optional_record(record: Optional[PartitionRecord]) -> Optional[Dict[str, Any]]:
    if record is None or record.state == PartitionState.MISSING.value:
        return None
    return record.to_dict()


def missing_record(role: str, *, reason: str) -> PartitionRecord:
    return PartitionRecord(
        disk_number=-1,
        partition_number=-1,
        role=role,
        state=PartitionState.MISSING.value,
        reason=reason,
    )


def unsupported_record(
    role: str,
    *,
    disk_number: int,
    partition_number: int,
    reason: str,
    gpt_type: Optional[str] = None,
    file_system: Optional[str] = None,
    label: Optional[str] = None,
    drive_letter: Optional[str] = None,
    size_bytes: Optional[int] = None,
    access_paths: tuple[str, ...] = (),
) -> PartitionRecord:
    return PartitionRecord(
        disk_number=disk_number,
        partition_number=partition_number,
        role=role,
        state=PartitionState.UNSUPPORTED.value,
        reason=reason,
        gpt_type=gpt_type,
        file_system=file_system,
        label=label,
        drive_letter=drive_letter,
        size_bytes=size_bytes,
        access_paths=access_paths,
    )

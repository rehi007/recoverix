"""Backup plan data structures (plan-only, no execution)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SourceInfo:
    """Source partitions and disk for backup."""

    efi_partition: Optional[str]
    windows_partition: str
    disk: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TargetInfo:
    """Recovery Image partition target metadata."""

    recovery_image_partition: str
    mount_required: bool
    expected_mount_point: str
    recovery_accessible: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackupCommands:
    """Planned command strings (not executed in step 9)."""

    gpt_backup: str
    efi_backup: str
    windows_partclone: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BackupPlan:
    """Full backup plan output."""

    status: str
    can_backup: bool
    dry_run: bool
    execution_allowed: bool
    reason: Optional[str]
    bitlocker: str = "UNKNOWN"
    source: Optional[SourceInfo] = None
    targets: Optional[TargetInfo] = None
    planned_steps: List[str] = field(default_factory=list)
    commands: Optional[BackupCommands] = None
    manifest_plan: Dict[str, Any] = field(default_factory=dict)
    sha256_plan: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "can_backup": self.can_backup,
            "dry_run": self.dry_run,
            "execution_allowed": self.execution_allowed,
            "reason": self.reason,
            "bitlocker": self.bitlocker,
            "source": self.source.to_dict() if self.source else None,
            "targets": self.targets.to_dict() if self.targets else None,
            "planned_steps": list(self.planned_steps),
            "commands": self.commands.to_dict() if self.commands else None,
            "manifest_plan": dict(self.manifest_plan),
            "sha256_plan": list(self.sha256_plan),
        }
        return payload

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

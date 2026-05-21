"""EFI rollback with Windows Boot Manager survivability priority."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.command import run_command
from common.logger import get_logger
from recovery_runtime.mounts import mount_point_for_label
from restore_engine.partclone_restore import BOOTMGFW_EFI_PATH
from restore_engine.restore_paths import EFI_SNAPSHOT_DIR
from restore_engine.restore_planner import _find_efi_mount
from restore_engine.restore_state import logs_dir

logger = get_logger(__name__)

BOOTMGFW_RELATIVE = Path(BOOTMGFW_EFI_PATH)
RECOVERY_BOOT_RELATIVE = Path("EFI/RecoveryBoot")


@dataclass
class EfiRollbackResult:
    """Outcome of an EFI rollback attempt."""

    success: bool
    status: str
    reason: Optional[str] = None
    bootmgfw_preserved: bool = False
    recoveryboot_restored: bool = False
    operations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RollbackLogWriter:
    """Non-fatal rollback logging under RECOVERY_IMAGE/logs."""

    def __init__(self, recovery_root: Path) -> None:
        self._dir = logs_dir(recovery_root)

    def append(self, filename: str, message: str) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._dir / filename
            stamp = datetime.now(timezone.utc).isoformat()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} | {message}\n")
        except OSError as exc:
            logger.debug("rollback log skipped: %s", exc)


def _resolve_efi_snapshot(recovery_root: Path) -> Path:
    return recovery_root / EFI_SNAPSHOT_DIR


def rollback_efi(
    *,
    recovery_root: Path,
    efi_partition: str,
    confirmed: bool,
    dry_run: bool = False,
) -> EfiRollbackResult:
    """
    Restore EFI survivability assets from pre-restore snapshot.

    Never overwrites bootmgfw.efi when the live file exists and snapshot is missing it.
    RecoveryBoot files are restored from snapshot when available.
    """
    operations: List[str] = []
    log = RollbackLogWriter(recovery_root)
    snapshot_root = _resolve_efi_snapshot(recovery_root)

    if not snapshot_root.is_dir():
        reason = f"EFI snapshot missing: {snapshot_root}"
        log.append("rollback.log", reason)
        return EfiRollbackResult(success=False, status="FAILED", reason=reason)

    mount = _find_efi_mount(efi_partition)
    created_mount = False
    if mount is None:
        mount = mount_point_for_label("ESP_ROLLBACK")
        mount.mkdir(parents=True, exist_ok=True)
        if dry_run:
            operations.append(f"planned mount {efi_partition} -> {mount}")
        else:
            result = run_command(
                ["mount", "-o", "rw", efi_partition, str(mount)],
                dry_run=False,
                confirmed=confirmed,
            )
            if result.returncode != 0:
                reason = f"EFI mount failed: {result.stderr.strip()}"
                log.append("error.log", reason)
                return EfiRollbackResult(success=False, status="FAILED", reason=reason)
            created_mount = True
            operations.append(f"mount {efi_partition} -> {mount}")

    try:
        live_bootmgfw = mount / BOOTMGFW_RELATIVE
        snap_bootmgfw = snapshot_root / BOOTMGFW_RELATIVE
        bootmgfw_preserved = live_bootmgfw.is_file()

        if snap_bootmgfw.is_file() and not live_bootmgfw.is_file():
            if dry_run:
                operations.append(f"planned restore {BOOTMGFW_RELATIVE}")
            else:
                snap_bootmgfw.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snap_bootmgfw, live_bootmgfw)
                bootmgfw_preserved = True
                operations.append(f"restored {BOOTMGFW_RELATIVE}")
        else:
            operations.append("bootmgfw.efi left unchanged (Windows survivability)")
            log.append("rollback.log", "bootmgfw.efi preserved")

        snap_recovery = snapshot_root / RECOVERY_BOOT_RELATIVE
        target_recovery = mount / RECOVERY_BOOT_RELATIVE
        recoveryboot_restored = False
        if snap_recovery.is_dir():
            if dry_run:
                operations.append(f"planned restore {RECOVERY_BOOT_RELATIVE}")
                recoveryboot_restored = True
            else:
                if target_recovery.exists():
                    shutil.rmtree(target_recovery)
                shutil.copytree(snap_recovery, target_recovery, dirs_exist_ok=True)
                recoveryboot_restored = True
                operations.append(f"restored {RECOVERY_BOOT_RELATIVE}")

        if not bootmgfw_preserved:
            reason = "bootmgfw.efi missing after EFI rollback"
            log.append("error.log", reason)
            return EfiRollbackResult(
                success=False,
                status="FAILED",
                reason=reason,
                bootmgfw_preserved=False,
                recoveryboot_restored=recoveryboot_restored,
                operations=operations,
            )

        log.append("rollback.log", "EFI rollback completed")
        return EfiRollbackResult(
            success=True,
            status="COMPLETED",
            bootmgfw_preserved=True,
            recoveryboot_restored=recoveryboot_restored,
            operations=operations,
        )
    except OSError as exc:
        reason = f"EFI rollback failed: {exc}"
        log.append("error.log", reason)
        return EfiRollbackResult(success=False, status="FAILED", reason=reason, operations=operations)
    finally:
        if created_mount and not dry_run:
            run_command(["umount", str(mount)], dry_run=False, confirmed=confirmed)

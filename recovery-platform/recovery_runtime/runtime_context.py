"""Runtime discovery and policy context for Recovery Runtime TUI."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backup_engine.backup_planner import (
    create_backup_plan,
    discover_layout,
    read_bitlocker_state,
)
from backup_engine.backup_state import has_incomplete_backup
from backup_engine.manifest import MANIFEST_FILENAME
from backup_engine.run_backup import build_disk_metadata
from boot_manager.firmware_reader import read_firmware_boot
from common.command import run_readonly
from common.logger import get_logger
from recovery_runtime.mounts import resolve_mount_path
from recovery_runtime.state import RuntimeState, apply_persisted_recovery_state
from restore_engine.restore_paths import EFI_SNAPSHOT_DIR
from restore_engine.restore_planner import build_restore_plan, verify_windows_boot_manager
from restore_engine.restore_state import is_restore_in_progress
from validation.image_validation import RestoreValidationResult, validate_restore

logger = get_logger(__name__)


@dataclass
class MenuAvailability:
    """Per-menu visibility and executability (visible != executable)."""

    backup_visible: bool = True
    backup_executable: bool = False
    backup_reason: Optional[str] = None
    backup_warning: Optional[str] = None
    restore_visible: bool = True
    restore_executable: bool = False
    restore_reason: Optional[str] = None
    delete_visible: bool = True
    delete_executable: bool = False
    delete_reason: Optional[str] = None


@dataclass
class RuntimeContext:
    """Aggregated runtime state for menu actions."""

    recovery_root: Optional[Path] = None
    recovery_image_partition: Optional[Any] = None
    recovery_linux_partition: Optional[Any] = None
    layout: Optional[Any] = None
    current_disk: Optional[Any] = None
    validation_result: Optional[RestoreValidationResult] = None
    restore_plan: Optional[Any] = None
    runtime_state: RuntimeState = field(default_factory=RuntimeState)
    firmware_state: Optional[Any] = None
    secure_boot_state: str = "UNKNOWN"
    bitlocker_state: str = "UNKNOWN"
    valid_backup: bool = False
    incomplete_backup: bool = False
    efi_rollback_available: bool = False
    menu: MenuAvailability = field(default_factory=MenuAvailability)
    status_lines: List[str] = field(default_factory=list)

    def refresh(self) -> None:
        """Recompute discovery, validation, and menu availability."""
        from recovery_runtime.discover import require_linux

        require_linux()
        self._discover()
        self._validate()
        self._apply_menu_policy()

    def _discover(self) -> None:
        from recovery_runtime.discover import discover_recovery_volumes

        image, linux, volumes = discover_recovery_volumes()
        self.recovery_image_partition = image
        self.recovery_linux_partition = linux
        self.runtime_state.recovery_image = image
        self.runtime_state.recovery_linux = linux
        self.runtime_state.all_volumes = volumes

        if image is None:
            self.recovery_root = None
            return

        mount = resolve_mount_path(image.mountpoint, "RECOVERY_IMAGE")
        self.recovery_root = mount
        if mount and mount.exists():
            apply_persisted_recovery_state(self.runtime_state, mount)

        topology_reason, layout = discover_layout()
        self.layout = layout
        if layout is not None:
            self.current_disk = build_disk_metadata(layout)

        self.bitlocker_state = read_bitlocker_state(live=True)
        self.secure_boot_state = read_secure_boot_state()
        self.firmware_state = read_firmware_boot(dry_run=True)

        if self.recovery_root:
            self.incomplete_backup = has_incomplete_backup(self.recovery_root)
            self.valid_backup = has_valid_recovery_backup(self.recovery_root)
            efi_snap = self.recovery_root / EFI_SNAPSHOT_DIR
            self.efi_rollback_available = efi_snap.is_dir()

    def _validate(self) -> None:
        self.validation_result = RestoreValidationResult(
            allowed=False,
            status="SKIPPED",
            reason="recovery image not mounted",
        )
        self.restore_plan = None
        if self.recovery_root is None or self.current_disk is None:
            return
        try:
            self.validation_result = validate_restore(
                self.recovery_root,
                self.current_disk,
            )
        except Exception as exc:
            logger.exception("validate_restore failed in runtime context")
            self.validation_result = RestoreValidationResult(
                allowed=False,
                status="REJECTED",
                reason="validation_exception",
                checks={"error": str(exc)},
            )
        try:
            self.restore_plan = build_restore_plan(live=True)
        except Exception as exc:
            logger.exception("build_restore_plan failed")
            self.restore_plan = None

        self._build_status_lines()

    def _build_status_lines(self) -> None:
        lines: List[str] = []
        lines.append(
            f"Recovery Image  : {'found' if self.recovery_image_partition else 'missing'}"
        )
        lines.append(
            f"Recovery Linux  : {'found' if self.recovery_linux_partition else 'missing'}"
        )
        lines.append(f"Valid backup    : {'yes' if self.valid_backup else 'no'}")
        lines.append(
            f"Incomplete mark : {'yes' if self.incomplete_backup else 'no'}"
        )
        if self.validation_result:
            lines.append(
                f"validate_restore: {self.validation_result.status} "
                f"({'PASS' if self.validation_result.allowed else 'FAIL'})"
            )
            if self.validation_result.reason:
                lines.append(f"  reason: {self.validation_result.reason}")
        if self.restore_plan:
            lines.append(
                f"restore_allowed : {self.restore_plan.restore_allowed}"
            )
        persisted = self.runtime_state.persisted
        if persisted:
            lines.append(
                f"rollback_required: {persisted.rollback_required}"
            )
            lines.append(
                f"restore_in_progress: {is_restore_in_progress(persisted)}"
            )
        bootmgr = (
            verify_windows_boot_manager(self.layout)
            if self.layout is not None
            else None
        )
        if bootmgr:
            lines.append(
                f"Windows Boot Mgr: {bootmgr.status} ({bootmgr.reason or 'ok'})"
            )
        lines.append(f"Secure Boot     : {self.secure_boot_state}")
        lines.append(f"BitLocker       : {self.bitlocker_state}")
        if self.firmware_state:
            order = getattr(self.firmware_state, "boot_order", [])
            lines.append(f"BootOrder       : {', '.join(order) if order else 'unknown'}")
        self.status_lines = lines

    def _apply_menu_policy(self) -> None:
        menu = MenuAvailability()
        menu.backup_visible = True
        menu.restore_visible = True
        menu.delete_visible = True

        if sys.platform != "linux":
            reason = "Recovery Runtime requires Linux"
            menu.backup_reason = reason
            menu.restore_reason = reason
            menu.delete_reason = reason
            self.menu = menu
            self._sync_runtime_flags(menu)
            return

        if self.recovery_linux_partition is None:
            reason = "RECOVERY_LINUX partition not found"
            menu.backup_reason = reason
            menu.restore_reason = reason
            menu.delete_reason = reason
            self.menu = menu
            self._sync_runtime_flags(menu)
            return

        if self.recovery_image_partition is None:
            menu.backup_reason = "RECOVERY_IMAGE partition not found"
            menu.restore_reason = menu.backup_reason
            menu.delete_reason = "valid backup 없음"
            self.menu = menu
            self._sync_runtime_flags(menu)
            return

        if self.recovery_root is None or not self.recovery_root.exists():
            menu.backup_reason = "RECOVERY_IMAGE not mounted"
            menu.restore_reason = menu.backup_reason
            self.menu = menu
            self._sync_runtime_flags(menu)
            return

        if self.bitlocker_state == "ON":
            menu.backup_reason = "BitLocker ON"
            menu.restore_reason = "BitLocker ON"
        elif self.valid_backup:
            menu.backup_reason = "기존 valid backup 존재"
        elif self.layout is None:
            menu.backup_reason = "backup layout discovery failed"
        else:
            plan = create_backup_plan()
            if plan.can_backup and plan.status in ("PLANNED", "MOUNT_REQUIRED"):
                menu.backup_executable = True
                menu.backup_reason = None
            else:
                menu.backup_reason = plan.reason or plan.status

        if self.incomplete_backup:
            menu.backup_warning = "incomplete_backup marker present"

        persisted = self.runtime_state.persisted
        if persisted and persisted.rollback_required:
            menu.restore_reason = "rollback_required=true"
        elif self.runtime_state.interrupted_restore:
            menu.restore_reason = "interrupted restore detected"
        elif is_restore_in_progress(persisted) if persisted else False:
            menu.restore_reason = "restore_in_progress=true"
        elif self.bitlocker_state == "ON":
            pass
        elif self.validation_result and not self.validation_result.allowed:
            menu.restore_reason = self._human_validation_reason(
                self.validation_result.reason
            )
        elif self.restore_plan and not self.restore_plan.restore_allowed:
            menu.restore_reason = self.restore_plan.reason or "restore not allowed"
        elif self.restore_plan and self.restore_plan.restore_allowed:
            menu.restore_executable = True
            menu.restore_reason = None
        else:
            menu.restore_reason = "restore plan unavailable"

        if self.valid_backup:
            menu.delete_executable = True
            menu.delete_reason = None
        else:
            menu.delete_reason = "valid backup 없음"

        self.menu = menu
        self._sync_runtime_flags(menu)

    def _sync_runtime_flags(self, menu: MenuAvailability) -> None:
        self.runtime_state.backup_enabled = menu.backup_executable
        self.runtime_state.restore_enabled = menu.restore_executable
        self.runtime_state.destructive_allowed = (
            menu.backup_executable or menu.restore_executable or menu.delete_executable
        )

    @staticmethod
    def _human_validation_reason(reason: Optional[str]) -> str:
        if not reason:
            return "validation failed"
        lowered = reason.lower()
        if "hash" in lowered:
            return "manifest hash mismatch"
        if "device" in lowered:
            return "device_id mismatch"
        if "incomplete" in lowered:
            return "incomplete backup detected"
        return reason


def read_secure_boot_state() -> str:
    """Best-effort Secure Boot state probe on Linux."""
    if sys.platform != "linux":
        return "UNKNOWN"
    for cmd in (
        ["mokutil", "--sb-state"],
        ["bootctl", "status"],
    ):
        result = run_readonly(cmd)
        if result.returncode != 0:
            continue
        text = (result.stdout + result.stderr).lower()
        if "enabled" in text or "secureboot enabled" in text:
            return "ON"
        if "disabled" in text:
            return "OFF"
    efi_vars = Path("/sys/firmware/efi/efivars")
    if efi_vars.is_dir():
        return "UNKNOWN (EFI variables present)"
    return "UNKNOWN"


def has_valid_recovery_backup(recovery_root: Path) -> bool:
    """True when finalized recovery-manifest.json indicates a complete backup."""
    if has_incomplete_backup(recovery_root):
        return False
    manifest_path = recovery_root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not data.get("backup_complete"):
        return False
    hashes = data.get("sha256_hashes") or {}
    return bool(hashes)


def build_runtime_context() -> RuntimeContext:
    """Construct and refresh runtime context for the TUI."""
    if sys.platform == "win32":
        raise RuntimeError("Recovery Runtime cannot run on Windows")
    from recovery_runtime.discover import require_linux

    require_linux()
    ctx = RuntimeContext()
    ctx.refresh()
    return ctx

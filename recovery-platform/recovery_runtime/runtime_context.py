"""Runtime discovery and policy context for Recovery Runtime TUI."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional

from backup_engine.backup_planner import (
    create_backup_plan,
    discover_layout,
    read_bitlocker_state,
)
from backup_engine.backup_finalize import find_manifest_path
from backup_engine.backup_state import has_incomplete_backup
from backup_engine.manifest import MANIFEST_FILENAME, load_recovery_manifest
from backup_engine.run_backup import build_disk_metadata
from boot_manager.firmware_reader import read_firmware_boot
from common.command import run_readonly
from common.logger import get_logger
from recovery_runtime.admin_bridge import helper_available, run_runtime_admin_json
from recovery_runtime.mounts import ensure_readonly_mount, resolve_mount_path
from recovery_runtime.state import RuntimeState, apply_persisted_recovery_state
from restore_engine.restore_paths import EFI_SNAPSHOT_DIR
from restore_engine.restore_planner import build_restore_plan, verify_windows_boot_manager
from restore_engine.restore_state import is_restore_in_progress
from validation.image_validation import RestoreValidationResult

logger = get_logger(__name__)
_BOOTSTRAP_LOG_PATH = Path("/tmp/recoverix-runtime-bootstrap.log")
UNSUPPORTED_BACKUP_TYPES = {"admin", "admin_compact", "compact", "compact_admin"}
UNSUPPORTED_BACKUP_REASON = (
    "Unsupported backup image detected. Delete this backup image and create a new standard backup."
)


def _append_bootstrap_log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        with _BOOTSTRAP_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | runtime_context: {message}\n")
    except OSError:
        pass


def _timed(label: str, func, *args, **kwargs):
    started = perf_counter()
    try:
        return func(*args, **kwargs)
    finally:
        elapsed_ms = (perf_counter() - started) * 1000.0
        _append_bootstrap_log(f"{label}: {elapsed_ms:.1f}ms")


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
    restore_compatible: bool = False
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

        refresh_started = perf_counter()
        _append_bootstrap_log("refresh: enter")
        require_linux()
        _append_bootstrap_log("refresh: require_linux ok")
        self._discover()
        _append_bootstrap_log("refresh: discover ok")
        self._validate()
        _append_bootstrap_log("refresh: validate ok")
        self._apply_menu_policy()
        _append_bootstrap_log("refresh: menu policy ok")
        _append_bootstrap_log(
            f"refresh: total {(perf_counter() - refresh_started) * 1000.0:.1f}ms"
        )

    def _discover(self) -> None:
        from recovery_runtime.discover import discover_recovery_volumes

        _append_bootstrap_log("discover: discover_recovery_volumes start")
        image, linux, volumes = _timed("discover: discover_recovery_volumes", discover_recovery_volumes)
        _append_bootstrap_log(
            f"discover: volumes image={getattr(image, 'path', None)} linux={getattr(linux, 'path', None)} count={len(volumes)}"
        )
        self.recovery_image_partition = image
        self.recovery_linux_partition = linux
        self.runtime_state.recovery_image = image
        self.runtime_state.recovery_linux = linux
        self.runtime_state.all_volumes = volumes

        if image is None:
            self.recovery_root = None
            self.runtime_state.manifest_present = False
            self.runtime_state.manifest_path = None
            _append_bootstrap_log("discover: image missing")
            return

        _append_bootstrap_log("discover: ensure_readonly_mount start")
        mount = _timed(
            "discover: ensure_readonly_mount",
            ensure_readonly_mount,
            image.path,
            image.mountpoint,
            "RECOVERY_IMAGE",
        )
        _append_bootstrap_log(f"discover: ensure_readonly_mount result={mount}")
        if mount is None:
            mount = resolve_mount_path(image.mountpoint, "RECOVERY_IMAGE")
            _append_bootstrap_log(f"discover: fallback mount path={mount}")
        self.recovery_root = mount
        if mount and mount.exists():
            manifest_path = _timed("discover: find_manifest_path", find_manifest_path, mount)
            self.runtime_state.manifest_present = manifest_path is not None
            self.runtime_state.manifest_path = str(manifest_path) if manifest_path else None
            _append_bootstrap_log("discover: apply_persisted_recovery_state start")
            _timed(
                "discover: apply_persisted_recovery_state",
                apply_persisted_recovery_state,
                self.runtime_state,
                mount,
            )
            _append_bootstrap_log("discover: apply_persisted_recovery_state ok")
        else:
            self.runtime_state.manifest_present = False
            self.runtime_state.manifest_path = None

        _append_bootstrap_log("discover: discover_layout start")
        topology_reason, layout = _timed("discover: discover_layout", discover_layout)
        _append_bootstrap_log(
            f"discover: discover_layout result reason={topology_reason!r} layout={'yes' if layout is not None else 'no'}"
        )
        self.layout = layout
        if layout is not None:
            _append_bootstrap_log("discover: build_disk_metadata start")
            self.current_disk = _timed("discover: build_disk_metadata", build_disk_metadata, layout)
            _append_bootstrap_log("discover: build_disk_metadata ok")

        _append_bootstrap_log("discover: read_bitlocker_state start")
        self.bitlocker_state = _timed("discover: read_bitlocker_state", read_bitlocker_state, live=True)
        _append_bootstrap_log(f"discover: read_bitlocker_state={self.bitlocker_state}")
        _append_bootstrap_log("discover: read_secure_boot_state start")
        self.secure_boot_state = _timed("discover: read_secure_boot_state", read_secure_boot_state)
        _append_bootstrap_log(f"discover: read_secure_boot_state={self.secure_boot_state}")
        _append_bootstrap_log("discover: read_firmware_boot start")
        self.firmware_state = _timed("discover: read_firmware_boot", read_firmware_boot, dry_run=True)
        _append_bootstrap_log("discover: read_firmware_boot ok")

        if self.recovery_root:
            _append_bootstrap_log("discover: backup state evaluation start")
            self.incomplete_backup = _timed(
                "discover: has_incomplete_backup", has_incomplete_backup, self.recovery_root
            )
            self.valid_backup = _timed(
                "discover: has_valid_recovery_backup", has_valid_recovery_backup, self.recovery_root
            )
            efi_snap = self.recovery_root / EFI_SNAPSHOT_DIR
            self.efi_rollback_available = efi_snap.is_dir()
            _append_bootstrap_log(
                f"discover: backup state incomplete={self.incomplete_backup} valid={self.valid_backup} efi_rollback={self.efi_rollback_available}"
            )

    def _validate(self) -> None:
        _append_bootstrap_log("validate: enter")
        self.validation_result = RestoreValidationResult(
            allowed=False,
            status="SKIPPED",
            reason="recovery image not mounted",
        )
        self.restore_plan = None
        if self.recovery_root is None or self.current_disk is None:
            _append_bootstrap_log(
                f"validate: skipped recovery_root={self.recovery_root} current_disk={'yes' if self.current_disk is not None else 'no'}"
            )
            return
        if helper_available():
            _append_bootstrap_log("validate: privileged restore check start")
            privileged = _timed("validate: privileged_restore_check", self._load_privileged_restore_status)
            if privileged:
                _append_bootstrap_log("validate: privileged restore check ok")
                self._build_status_lines()
                _append_bootstrap_log("validate: build_status_lines ok")
                return
            _append_bootstrap_log("validate: privileged restore check unavailable; fallback")
        try:
            _append_bootstrap_log("validate: build_restore_plan start")
            self.restore_plan = _timed(
                "validate: build_restore_plan",
                build_restore_plan,
                live=True,
                fast_validation=True,
            )
            _append_bootstrap_log(
                f"validate: build_restore_plan ok allowed={getattr(self.restore_plan, 'restore_allowed', None)}"
            )
        except Exception as exc:
            logger.exception("build_restore_plan failed")
            _append_bootstrap_log(f"validate: build_restore_plan exception={exc}")
            self.restore_plan = None
            self.validation_result = RestoreValidationResult(
                allowed=False,
                status="REJECTED",
                reason="validation_exception",
                checks={"error": str(exc)},
            )
        else:
            validation = getattr(self.restore_plan, "validation", {}) or {}
            self.validation_result = RestoreValidationResult(
                allowed=bool(validation.get("allowed", False)),
                status=str(validation.get("status") or "REJECTED"),
                reason=validation.get("reason"),
                checks=validation.get("checks") or {},
            )

        _append_bootstrap_log("validate: build_status_lines start")
        self._build_status_lines()
        _append_bootstrap_log("validate: build_status_lines ok")

    def _load_privileged_restore_status(self) -> bool:
        rc, payload, stderr = run_runtime_admin_json("check-restore")
        if rc != 0 or payload.get("status") != "COMPLETED":
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            logger.warning("privileged restore check failed: %s", reason)
            return False

        validation = payload.get("validation") or {}
        self.validation_result = RestoreValidationResult(
            allowed=bool(validation.get("allowed", False)),
            status=str(validation.get("status") or "REJECTED"),
            reason=validation.get("reason"),
            checks=validation.get("checks") or {},
        )

        plan = payload.get("restore_plan")
        if isinstance(plan, dict):
            self.restore_plan = SimpleNamespace(**plan)
        else:
            self.restore_plan = None
        return True

    def _build_status_lines(self) -> None:
        status_started = perf_counter()
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
            restore_mode = getattr(self.restore_plan, "restore_mode", None)
            if restore_mode:
                lines.append(f"restore_mode    : {restore_mode}")
        persisted = self.runtime_state.persisted
        if persisted:
            lines.append(
                f"rollback_required: {persisted.rollback_required}"
            )
            lines.append(
                f"restore_in_progress: {is_restore_in_progress(persisted)}"
            )
        bootmgr = (
            _timed("status_lines: verify_windows_boot_manager", verify_windows_boot_manager, self.layout)
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
        _append_bootstrap_log(
            f"status_lines: total {(perf_counter() - status_started) * 1000.0:.1f}ms"
        )

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
            menu.delete_reason = "no valid backup"
            self.menu = menu
            self._sync_runtime_flags(menu)
            return

        if self.recovery_root is None or not self.recovery_root.exists():
            menu.backup_reason = "RECOVERY_IMAGE not mounted"
            menu.restore_reason = menu.backup_reason
            self.menu = menu
            self._sync_runtime_flags(menu)
            return

        unsupported_backup = self._has_unsupported_backup()

        if unsupported_backup:
            menu.backup_reason = UNSUPPORTED_BACKUP_REASON
        elif self.bitlocker_state == "ON":
            menu.backup_reason = "BitLocker ON"
            menu.restore_reason = "BitLocker ON"
        elif self.valid_backup:
            menu.backup_reason = "valid backup already exists"
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
        if unsupported_backup:
            menu.restore_reason = UNSUPPORTED_BACKUP_REASON
        elif persisted and persisted.rollback_required:
            menu.restore_reason = "rollback_required=true"
        elif self.runtime_state.interrupted_restore:
            menu.restore_reason = "interrupted restore detected"
        elif is_restore_in_progress(persisted) if persisted else False:
            menu.restore_reason = "restore_in_progress=true"
        elif self.bitlocker_state == "ON":
            pass
        elif self.validation_result and not self.validation_result.allowed:
            menu.restore_reason = self._human_validation_reason(
                self.validation_result.reason,
                self.validation_result.checks,
            )
        elif self.restore_plan and not self.restore_plan.restore_allowed:
            menu.restore_reason = self.restore_plan.reason or "restore not allowed"
        elif self.restore_plan and self.restore_plan.restore_allowed:
            menu.restore_executable = True
            menu.restore_compatible = bool(
                getattr(self.restore_plan, "compatible_restore", False)
            )
            menu.restore_reason = None
        else:
            menu.restore_reason = "restore plan unavailable"

        if self.valid_backup:
            menu.delete_executable = True
            menu.delete_reason = None
        else:
            menu.delete_reason = "no valid backup"

        self.menu = menu
        self._sync_runtime_flags(menu)

    def _has_unsupported_backup(self) -> bool:
        if not self.recovery_root or not self.valid_backup:
            return False
        try:
            manifest = load_recovery_manifest(self.recovery_root)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        backup_type = str(
            manifest.get("backup_type") or manifest.get("backup_mode") or "standard"
        ).strip().lower()
        backup_type = backup_type.replace("-", "_").replace(" ", "_")
        return backup_type in UNSUPPORTED_BACKUP_TYPES

    def _sync_runtime_flags(self, menu: MenuAvailability) -> None:
        self.runtime_state.backup_enabled = menu.backup_executable
        self.runtime_state.restore_enabled = menu.restore_executable
        self.runtime_state.destructive_allowed = (
            menu.backup_executable or menu.restore_executable or menu.delete_executable
        )

    @staticmethod
    def _human_validation_reason(reason: Optional[str], checks: Optional[Dict[str, Any]] = None) -> str:
        if not reason:
            return "validation failed"
        lowered = reason.lower()
        if lowered == "validation_exception":
            detail = str((checks or {}).get("error") or "").strip()
            if detail:
                return f"validation_exception: {detail}"
            return "validation_exception"
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
    try:
        data = load_recovery_manifest(recovery_root)
    except json.JSONDecodeError:
        return False
    except FileNotFoundError:
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

    started = perf_counter()
    _append_bootstrap_log("build_runtime_context: enter")
    require_linux()
    _append_bootstrap_log("build_runtime_context: require_linux ok")
    ctx = RuntimeContext()
    _append_bootstrap_log("build_runtime_context: RuntimeContext created")
    ctx.refresh()
    _append_bootstrap_log("build_runtime_context: refresh complete")
    _append_bootstrap_log(
        f"build_runtime_context: total {(perf_counter() - started) * 1000.0:.1f}ms"
    )
    return ctx

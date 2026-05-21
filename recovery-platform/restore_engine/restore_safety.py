"""Final safety gate before destructive restore execution."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backup_engine.backup_planner import discover_layout, read_bitlocker_state
from backup_engine.backup_state import has_incomplete_backup
from backup_engine.manifest import (
    MANIFEST_FILENAME,
    DiskMetadata,
    compute_device_id,
    load_recovery_manifest,
)
from backup_engine.run_backup import build_disk_metadata
from common.errors import (
    BitLockerActiveError,
    ConfirmationRequiredError,
    InvalidConfirmationPhraseError,
    RestoreEnvironmentError,
    RestoreSafetyError,
)
from common.logger import get_logger
from recovery_runtime.discover import discover_recovery_volumes, require_linux
from restore_engine.confirmation import (
    format_target_disk_display,
    require_confirmation_phrase,
    verify_confirmation_phrase,
)
from validation.image_validation import validate_restore

logger = get_logger(__name__)


@dataclass(frozen=True)
class RestoreSafetyCheck:
    """Outcome of a single safety rule."""

    name: str
    passed: bool
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RestoreSafetyResult:
    """Aggregated restore safety evaluation."""

    allowed: bool
    status: str
    reason: Optional[str] = None
    apply: bool = False
    confirmed: bool = False
    phrase_verified: bool = False
    target_disk: Dict[str, str] = field(default_factory=dict)
    target_disk_display: List[str] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    failure_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def is_windows_environment() -> bool:
    return sys.platform == "win32"


def is_recovery_runtime_environment() -> RestoreSafetyCheck:
    """Require Linux recovery runtime with RECOVERY_LINUX partition present."""
    if is_windows_environment():
        return RestoreSafetyCheck(
            name="recovery_runtime",
            passed=False,
            reason="Windows environment cannot execute restore",
        )
    try:
        require_linux()
    except RuntimeError as exc:
        return RestoreSafetyCheck(
            name="recovery_runtime",
            passed=False,
            reason=str(exc),
        )

    _image, recovery_linux, _volumes = discover_recovery_volumes()
    if recovery_linux is None:
        return RestoreSafetyCheck(
            name="recovery_runtime",
            passed=False,
            reason="not inside Recovery Runtime (RECOVERY_LINUX partition not found)",
            details={"recovery_linux_found": False},
        )
    return RestoreSafetyCheck(
        name="recovery_runtime",
        passed=True,
        details={
            "recovery_linux_found": True,
            "recovery_linux_path": recovery_linux.path,
        },
    )


def check_apply_confirm_flags(*, apply: bool, confirmed: bool) -> RestoreSafetyCheck:
    if not apply:
        return RestoreSafetyCheck(
            name="apply_confirm",
            passed=False,
            reason="--apply is required to authorize restore execution",
            details={"apply": apply, "confirmed": confirmed},
        )
    if not confirmed:
        return RestoreSafetyCheck(
            name="apply_confirm",
            passed=False,
            reason="--apply requires --confirm; execution refused",
            details={"apply": apply, "confirmed": confirmed},
        )
    return RestoreSafetyCheck(
        name="apply_confirm",
        passed=True,
        details={"apply": apply, "confirmed": confirmed},
    )


def check_bitlocker_state(*, live: bool) -> RestoreSafetyCheck:
    state = read_bitlocker_state(live=live)
    if state == "ON":
        return RestoreSafetyCheck(
            name="bitlocker",
            passed=False,
            reason="BitLocker is ON; restore refused",
            details={"bitlocker": state},
        )
    return RestoreSafetyCheck(
        name="bitlocker",
        passed=True,
        details={"bitlocker": state},
    )


def check_incomplete_backup_marker(recovery_root: Path) -> RestoreSafetyCheck:
    present = has_incomplete_backup(recovery_root)
    if present:
        return RestoreSafetyCheck(
            name="incomplete_backup",
            passed=False,
            reason="incomplete_backup marker present; restore forbidden",
            details={"marker": str(recovery_root / "state/incomplete_backup")},
        )
    return RestoreSafetyCheck(
        name="incomplete_backup",
        passed=True,
        details={"marker_present": False},
    )


def check_device_id_match(
    manifest: Dict[str, Any],
    disk: DiskMetadata,
) -> RestoreSafetyCheck:
    expected = manifest.get("device_id")
    actual = compute_device_id(disk)
    details = {
        "manifest_device_id": expected,
        "current_device_id": actual,
    }
    if not expected:
        return RestoreSafetyCheck(
            name="device_id",
            passed=False,
            reason="manifest device_id missing; restore forbidden",
            details=details,
        )
    if expected != actual:
        return RestoreSafetyCheck(
            name="device_id",
            passed=False,
            reason="manifest device_id does not match current target device",
            details=details,
        )
    return RestoreSafetyCheck(name="device_id", passed=True, details=details)


def check_manifest_validation(
    recovery_root: Path,
    disk: DiskMetadata,
) -> RestoreSafetyCheck:
    try:
        validation = validate_restore(recovery_root, disk)
    except Exception as exc:
        logger.exception("validate_restore failed during safety gate")
        return RestoreSafetyCheck(
            name="validate_restore",
            passed=False,
            reason="validation_exception",
            details={"error": str(exc)},
        )
    return RestoreSafetyCheck(
        name="validate_restore",
        passed=validation.allowed,
        reason=validation.reason,
        details=validation.to_dict(),
    )


def _resolve_recovery_root() -> tuple[Optional[Path], Optional[str], Optional[Any]]:
    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        return None, topology_reason or "layout discovery failed", layout
    mount = layout.recovery_image.mountpoint
    if not mount:
        return None, "RECOVERY_IMAGE must be mounted for restore safety checks", layout
    root = Path(mount)
    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None, f"{MANIFEST_FILENAME} not found on mounted recovery image", layout
    return root, None, layout


def evaluate_restore_safety(
    *,
    apply: bool,
    confirmed: bool,
    confirmation_phrase: Optional[str] = None,
    recovery_root: Optional[Path] = None,
    live: bool = True,
) -> RestoreSafetyResult:
    """
    Run all restore safety checks (read-only except phrase verification).

    Does not execute destructive restore operations.
    """
    checks: List[RestoreSafetyCheck] = []
    failure_reasons: List[str] = []

    def _record(check: RestoreSafetyCheck) -> None:
        checks.append(check)
        if not check.passed and check.reason:
            failure_reasons.append(check.reason)

    _record(check_apply_confirm_flags(apply=apply, confirmed=confirmed))

    if is_windows_environment():
        _record(
            RestoreSafetyCheck(
                name="windows_forbidden",
                passed=False,
                reason="restore execution is forbidden on Windows",
            )
        )
        return _finalize(
            apply,
            confirmed,
            checks,
            failure_reasons,
            target_disk={
                "disk_guid": "unknown",
                "disk_serial": "unknown",
                "disk_model": "unknown",
                "device_id": "unknown",
            },
            target_disk_display=[],
        )

    _record(is_recovery_runtime_environment())
    _record(check_bitlocker_state(live=live))

    layout = None
    disk = DiskMetadata(
        disk_guid="unknown",
        disk_model="unknown",
        disk_serial="unknown",
        disk_size=0,
        windows_partition_uuid="unknown",
        efi_partition_uuid="unknown",
    )
    manifest_device_id: Optional[str] = None
    root = recovery_root

    if root is None:
        root, mount_reason, layout = _resolve_recovery_root()
        if mount_reason:
            _record(
                RestoreSafetyCheck(
                    name="recovery_mount",
                    passed=False,
                    reason=mount_reason,
                )
            )
    elif layout is None:
        _topology_reason, layout = discover_layout()

    if root is not None:
        _record(check_incomplete_backup_marker(root))
        if layout is not None:
            disk = build_disk_metadata(layout)
            try:
                manifest = load_recovery_manifest(root)
                manifest_device_id = manifest.get("device_id")
                _record(check_device_id_match(manifest, disk))
                _record(check_manifest_validation(root, disk))
            except Exception as exc:
                logger.exception("manifest load failed during safety gate")
                _record(
                    RestoreSafetyCheck(
                        name="manifest_load",
                        passed=False,
                        reason="manifest_load_failed",
                        details={"error": str(exc)},
                    )
                )

    display = format_target_disk_display(disk, manifest_device_id=manifest_device_id)
    phrase_verified = False
    if apply and confirmed:
        phrase_verified = verify_confirmation_phrase(confirmation_phrase)
        if not phrase_verified:
            failure_reasons.append(
                'confirmation phrase must be exactly: "RESTORE THIS DEVICE"'
            )
            checks.append(
                RestoreSafetyCheck(
                    name="confirmation_phrase",
                    passed=False,
                    reason="invalid or missing confirmation phrase",
                    details={"phrase_provided": confirmation_phrase is not None},
                )
            )
        else:
            checks.append(
                RestoreSafetyCheck(
                    name="confirmation_phrase",
                    passed=True,
                )
            )

    target_disk = {
        "disk_guid": disk.disk_guid,
        "disk_serial": disk.disk_serial,
        "disk_model": disk.disk_model,
        "device_id": compute_device_id(disk),
    }
    if manifest_device_id is not None:
        target_disk["manifest_device_id"] = manifest_device_id

    return _finalize(
        apply,
        confirmed,
        checks,
        failure_reasons,
        target_disk=target_disk,
        target_disk_display=display,
        phrase_verified=phrase_verified,
    )


def _finalize(
    apply: bool,
    confirmed: bool,
    checks: List[RestoreSafetyCheck],
    failure_reasons: List[str],
    *,
    target_disk: Dict[str, str],
    target_disk_display: List[str],
    phrase_verified: bool = False,
) -> RestoreSafetyResult:
    allowed = all(c.passed for c in checks) and apply and confirmed
    status = "PASS" if allowed else "REJECTED"
    reason = None if allowed else "; ".join(failure_reasons) or "restore execution not authorized"
    return RestoreSafetyResult(
        allowed=allowed,
        status=status,
        reason=reason,
        apply=apply,
        confirmed=confirmed,
        phrase_verified=phrase_verified,
        target_disk=target_disk,
        target_disk_display=target_disk_display,
        checks=[c.to_dict() for c in checks],
        failure_reasons=failure_reasons,
    )


def authorize_restore_execution(
    *,
    apply: bool,
    confirmed: bool,
    confirmation_phrase: Optional[str] = None,
    recovery_root: Optional[Path] = None,
    live: bool = True,
) -> RestoreSafetyResult:
    """
    Evaluate safety checks and raise when restore execution must not proceed.

    Call before any destructive restore step (partclone, GPT load, etc.).
    """
    if apply and not confirmed:
        raise ConfirmationRequiredError(
            "Refusing restore execution without --confirm"
        )

    result = evaluate_restore_safety(
        apply=apply,
        confirmed=confirmed,
        confirmation_phrase=confirmation_phrase,
        recovery_root=recovery_root,
        live=live,
    )

    if result.allowed:
        return result

    for check in result.checks:
        name = check.get("name")
        passed = check.get("passed", True)
        if passed:
            continue
        if name == "bitlocker":
            raise BitLockerActiveError(check.get("reason") or "BitLocker is ON")
        if name in ("recovery_runtime", "windows_forbidden"):
            raise RestoreEnvironmentError(check.get("reason") or "invalid environment")

    if apply and confirmed and not result.phrase_verified:
        disk = DiskMetadata(
            disk_guid=result.target_disk.get("disk_guid", "unknown"),
            disk_model=result.target_disk.get("disk_model", "unknown"),
            disk_serial=result.target_disk.get("disk_serial", "unknown"),
            disk_size=0,
            windows_partition_uuid="unknown",
            efi_partition_uuid="unknown",
        )
        require_confirmation_phrase(
            confirmation_phrase,
            disk=disk,
            manifest_device_id=result.target_disk.get("manifest_device_id"),
        )

    raise RestoreSafetyError(result.reason or "restore safety checks failed")

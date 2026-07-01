"""Restore validation for recovery images and manifests."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from backup_engine.backup_state import DEFAULT_IMAGE_FILES, has_incomplete_backup, is_partial_backup
from backup_engine.hash import sha256_file
from backup_engine.manifest import (
    MANIFEST_FILENAME,
    MANIFEST_HASH_FILENAME,
    DiskMetadata,
    compute_device_id,
    compute_manifest_hash,
    load_manifest_hash,
    load_recovery_manifest,
)
from common.logger import get_logger

logger = get_logger(__name__)
UNSUPPORTED_BACKUP_TYPES = {"admin", "admin_compact", "compact", "compact_admin"}
UNSUPPORTED_BACKUP_REASON = (
    "Unsupported backup image detected. Delete this backup image and create a new standard backup."
)


@dataclass(frozen=True)
class RestoreValidationResult:
    """Result of pre-restore validation."""

    allowed: bool
    status: str
    reason: Optional[str] = None
    checks: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _reject(reason: str, checks: Dict[str, Any]) -> RestoreValidationResult:
    logger.warning("restore validation rejected: %s", reason)
    return RestoreValidationResult(
        allowed=False,
        status="REJECTED",
        reason=reason,
        checks=checks,
    )


def _accept(
    checks: Dict[str, Any],
    *,
    status: str = "PASS",
    reason: Optional[str] = None,
) -> RestoreValidationResult:
    return RestoreValidationResult(
        allowed=True,
        status=status,
        reason=reason,
        checks=checks,
    )


def validate_incomplete_marker(recovery_root: Path) -> RestoreValidationResult:
    marker = recovery_root / "state/incomplete_backup"
    checks = {"incomplete_marker": str(marker), "present": marker.is_file()}
    if has_incomplete_backup(recovery_root):
        return _reject("incomplete backup marker present; restore forbidden", checks)
    return _accept(checks)


def validate_partial_backup_state(recovery_root: Path) -> RestoreValidationResult:
    candidates = [
        recovery_root / "manifests" / MANIFEST_FILENAME,  # canonical
        recovery_root / "metadata" / MANIFEST_FILENAME,  # legacy
        recovery_root / MANIFEST_FILENAME,  # legacy root
    ]
    manifest_exists = any(p.is_file() for p in candidates)
    partial = is_partial_backup(
        recovery_root,
        DEFAULT_IMAGE_FILES.values(),
        manifest_exists=manifest_exists,
    )
    checks = {
        "partial_backup": partial,
        "manifest_exists": manifest_exists,
    }
    if partial:
        return _reject("partial backup detected; restore forbidden", checks)
    return _accept(checks)


def validate_manifest_present(recovery_root: Path) -> RestoreValidationResult:
    candidates = [
        recovery_root / "manifests" / MANIFEST_FILENAME,  # canonical
        recovery_root / "metadata" / MANIFEST_FILENAME,  # legacy
        recovery_root / MANIFEST_FILENAME,  # legacy root
    ]
    manifest_path = next((p for p in candidates if p.is_file()), None)
    checks = {
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest_exists": manifest_path is not None,
    }
    if manifest_path is None:
        return _reject("manifest missing; restore forbidden", checks)
    return _accept(checks)


def validate_device_match(
    manifest: Dict[str, Any],
    current_disk: DiskMetadata,
) -> RestoreValidationResult:
    expected = manifest.get("device_id")
    actual = compute_device_id(current_disk)
    checks = {
        "expected_device_id": expected,
        "actual_device_id": actual,
    }
    if not expected:
        return _reject("manifest device_id missing; restore forbidden", checks)
    if expected != actual:
        return _reject("device mismatch; restore forbidden", checks)
    return _accept(checks)


def validate_device_compatible(
    manifest: Dict[str, Any],
    current_disk: DiskMetadata,
) -> RestoreValidationResult:
    expected = manifest.get("device_id")
    actual = compute_device_id(current_disk)
    checks = {
        "expected_device_id": expected,
        "actual_device_id": actual,
        "compatible_restore": True,
    }
    if not expected:
        return _reject("manifest device_id missing; restore forbidden", checks)
    if expected == actual:
        return _accept({**checks, "device_match": True})
    return _accept(
        {**checks, "device_match": False},
        status="COMPATIBLE",
        reason="device mismatch accepted for compatible restore",
    )


def validate_backup_type_supported(manifest: Dict[str, Any]) -> RestoreValidationResult:
    backup_type = str(
        manifest.get("backup_type") or manifest.get("backup_mode") or "standard"
    ).strip().lower()
    backup_type = backup_type.replace("-", "_").replace(" ", "_")
    checks = {"backup_type": backup_type}
    if backup_type in UNSUPPORTED_BACKUP_TYPES:
        return _reject(UNSUPPORTED_BACKUP_REASON, checks)
    return _accept(checks)


def validate_file_hashes(
    recovery_root: Path,
    manifest: Dict[str, Any],
) -> RestoreValidationResult:
    declared = manifest.get("sha256_hashes") or {}
    if not declared:
        return _reject("manifest sha256_hashes missing; restore forbidden", {"declared": declared})

    mismatches: Dict[str, Dict[str, str]] = {}
    for relative, expected_hash in declared.items():
        file_path = recovery_root / relative
        if not file_path.is_file():
            mismatches[relative] = {
                "expected": expected_hash,
                "actual": "FILE_MISSING",
            }
            continue
        actual_hash = sha256_file(file_path)
        if actual_hash != expected_hash:
            mismatches[relative] = {
                "expected": expected_hash,
                "actual": actual_hash,
            }

    checks = {"files_checked": len(declared), "mismatches": mismatches}
    if mismatches:
        return _reject("hash mismatch; restore forbidden", checks)
    return _accept(checks)


def validate_manifest_hash_file(
    recovery_root: Path,
    manifest: Dict[str, Any],
) -> RestoreValidationResult:
    sidecar = load_manifest_hash(recovery_root)
    computed = compute_manifest_hash(manifest)
    hash_path = recovery_root / MANIFEST_HASH_FILENAME
    checks = {
        "sidecar_hash": sidecar,
        "computed_hash": computed,
        "hash_path": str(hash_path),
        "hash_exists": hash_path.is_file(),
    }
    if sidecar is None:
        return _reject("manifest hash missing; restore forbidden", checks)
    if sidecar != computed:
        return _reject("manifest hash mismatch; restore forbidden", checks)
    return _accept(checks)


def _validate_restore_impl(
    recovery_root: Path,
    current_disk: DiskMetadata,
    *,
    verify_file_hashes: bool,
    compatible_restore: bool = False,
) -> RestoreValidationResult:
    checks: Dict[str, Any] = {}

    incomplete = validate_incomplete_marker(recovery_root)
    checks["incomplete_marker"] = incomplete.to_dict()
    if not incomplete.allowed:
        return incomplete

    partial = validate_partial_backup_state(recovery_root)
    checks["partial_backup"] = partial.to_dict()
    if not partial.allowed:
        return partial

    present = validate_manifest_present(recovery_root)
    checks["manifest_present"] = present.to_dict()
    if not present.allowed:
        return present

    manifest = load_recovery_manifest(recovery_root)

    backup_type = validate_backup_type_supported(manifest)
    checks["backup_type"] = backup_type.to_dict()
    if not backup_type.allowed:
        return backup_type

    device = (
        validate_device_compatible(manifest, current_disk)
        if compatible_restore
        else validate_device_match(manifest, current_disk)
    )
    checks["device"] = device.to_dict()
    if not device.allowed:
        return device

    if verify_file_hashes:
        hashes = validate_file_hashes(recovery_root, manifest)
        checks["hashes"] = hashes.to_dict()
        if not hashes.allowed:
            return hashes
    else:
        checks["hashes"] = {
            "allowed": True,
            "status": "SKIPPED",
            "reason": "deferred until restore start",
            "checks": {"mode": "quick"},
        }

    manifest_hash = validate_manifest_hash_file(recovery_root, manifest)
    checks["manifest_hash"] = manifest_hash.to_dict()
    if not manifest_hash.allowed:
        return manifest_hash

    if compatible_restore and device.status == "COMPATIBLE":
        return _accept(
            checks,
            status="COMPATIBLE",
        )

    return _accept(checks)


def validate_restore(
    recovery_root: Path,
    current_disk: DiskMetadata,
) -> RestoreValidationResult:
    """
    Run full restore validation (fail-closed).

    Restore allowed only when ALL pass:
    - no incomplete_backup marker
    - not partial backup
    - manifest exists
    - device_id match
    - image hashes match
    - manifest hash sidecar valid
    """
    try:
        return _validate_restore_impl(
            recovery_root,
            current_disk,
            verify_file_hashes=True,
        )
    except Exception as exc:
        logger.exception("restore validation exception")
        return RestoreValidationResult(
            allowed=False,
            status="REJECTED",
            reason="validation_exception",
            checks={"error": str(exc)},
        )


def validate_restore_compatible(
    recovery_root: Path,
    current_disk: DiskMetadata,
) -> RestoreValidationResult:
    """
    Full validation for disk replacement / hard-copy restore.

    This mode accepts a device_id mismatch only. It still rejects incomplete
    backups, partial backups, missing manifests, image hash mismatches, and
    manifest hash mismatches.
    """
    try:
        return _validate_restore_impl(
            recovery_root,
            current_disk,
            verify_file_hashes=True,
            compatible_restore=True,
        )
    except Exception as exc:
        logger.exception("compatible restore validation exception")
        return RestoreValidationResult(
            allowed=False,
            status="REJECTED",
            reason="validation_exception",
            checks={"error": str(exc)},
        )


def validate_restore_quick(
    recovery_root: Path,
    current_disk: DiskMetadata,
) -> RestoreValidationResult:
    """
    Fast restore-readiness validation for menu/preflight use.

    Skips full artifact SHA256 over large backup images. The destructive restore
    path still runs full validate_restore() before any write.
    """
    try:
        return _validate_restore_impl(
            recovery_root,
            current_disk,
            verify_file_hashes=False,
        )
    except Exception as exc:
        logger.exception("restore validation exception")
        return RestoreValidationResult(
            allowed=False,
            status="REJECTED",
            reason="validation_exception",
            checks={"error": str(exc)},
        )


def validate_restore_compatible_quick(
    recovery_root: Path,
    current_disk: DiskMetadata,
) -> RestoreValidationResult:
    """
    Fast compatible restore-readiness validation for menu/preflight use.

    Full artifact SHA256 checks are still performed by the destructive restore
    safety gate before writing to disk.
    """
    try:
        return _validate_restore_impl(
            recovery_root,
            current_disk,
            verify_file_hashes=False,
            compatible_restore=True,
        )
    except Exception as exc:
        logger.exception("compatible restore validation exception")
        return RestoreValidationResult(
            allowed=False,
            status="REJECTED",
            reason="validation_exception",
            checks={"error": str(exc)},
        )

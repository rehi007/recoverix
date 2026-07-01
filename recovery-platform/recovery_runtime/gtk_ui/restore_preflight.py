"""Restore preflight validation (non-destructive; no partclone/restore execution)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backup_engine.backup_planner import discover_layout, read_bitlocker_state
from backup_engine.manifest import MANIFEST_FILENAME
from backup_engine.run_backup import build_disk_metadata
from common.command import run_readonly
from recovery_runtime.gtk_ui.image_status import ImageStatus, probe_recovery_image
from recovery_runtime.gtk_ui.logging_util import restore_preflight_log
from validation.image_validation import validate_restore

DEV_MODE_DISABLED_MSG = "Actual restore execution is disabled in development mode."


@dataclass
class RestorePreflightResult:
    """Restore preflight snapshot (JSON-serializable)."""

    restore_preflight_ok: bool
    restore_enabled: bool
    development_mode: bool
    recovery_image_found: bool = False
    valid_backup_exists: bool = False
    manifest_exists: bool = False
    hashes_exist: bool = False
    system_image_exists: bool = False
    esp_image_exists: bool = False
    windows_partition_found: bool = False
    efi_partition_found: bool = False
    bitlocker_detected: bool = False
    unsupported_topology: bool = False
    sha256_validation_ready: bool = False
    windows_partition: Optional[str] = None
    efi_partition: Optional[str] = None
    recovery_image_mount: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_json_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json_dict(), indent=indent, sort_keys=True)


def development_mode_enabled() -> bool:
    """Development mode blocks actual restore execution (default: on)."""
    return os.environ.get("RECOVERIX_RESTORE_DEV", "1").strip() not in ("0", "false", "False", "no")


def _manifest_paths(recovery_root: Path) -> List[Path]:
    return [
        recovery_root / MANIFEST_FILENAME,
        recovery_root / "metadata" / MANIFEST_FILENAME,
        recovery_root / "manifests" / "recovery-manifest.json",
    ]


def _manifest_exists_on_root(recovery_root: Path) -> bool:
    return any(p.is_file() for p in _manifest_paths(recovery_root))


def _detect_bitlocker_skeleton(windows_path: Optional[str]) -> bool:
    state = read_bitlocker_state(live=True)
    if state == "ON":
        restore_preflight_log("BitLocker detected via read_bitlocker_state=ON")
        return True
    if not windows_path:
        return False
    result = run_readonly(["blkid", "-o", "value", "-s", "TYPE", windows_path])
    if result.returncode == 0 and "bitlocker" in (result.stdout or "").lower():
        restore_preflight_log(f"BitLocker skeleton detected on {windows_path} (blkid TYPE)")
        return True
    return False


def _run_hash_validation(recovery_root: Path) -> tuple[bool, Optional[str]]:
    """Run full validate_restore when engine manifest exists at recovery root."""
    if not _manifest_exists_on_root(recovery_root):
        restore_preflight_log(
            "SHA256 validation skipped: engine manifest not at recovery root "
            f"({MANIFEST_FILENAME})"
        )
        return False, None

    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        return False, topology_reason or "layout discovery failed for hash validation"

    disk = build_disk_metadata(layout)
    restore_preflight_log("manifest validation: running validate_restore")
    validation = validate_restore(recovery_root, disk)
    restore_preflight_log(
        f"hashes validation: allowed={validation.allowed} status={validation.status} "
        f"reason={validation.reason or '-'}"
    )
    if not validation.allowed:
        return False, validation.reason or "hash or manifest validation failed"
    return True, None


def run_restore_preflight() -> RestorePreflightResult:
    """Run non-destructive restore preflight checks."""
    dev_mode = development_mode_enabled()
    errors: List[str] = []
    warnings: List[str] = []

    restore_preflight_log("restore preflight start")

    result = RestorePreflightResult(
        restore_preflight_ok=False,
        restore_enabled=False,
        development_mode=dev_mode,
    )

    if dev_mode:
        warnings.append(DEV_MODE_DISABLED_MSG)

    image = probe_recovery_image()
    result.recovery_image_found = image.recovery_image_partition_found
    result.valid_backup_exists = image.valid_backup_exists
    result.manifest_exists = image.manifest_exists
    result.hashes_exist = image.hashes_exist
    result.system_image_exists = image.system_image_exists
    result.esp_image_exists = image.esp_image_exists

    if image.mount_point is not None:
        result.recovery_image_mount = str(image.mount_point)

    restore_preflight_log(
        f"RECOVERY_IMAGE probe: found={result.recovery_image_found} "
        f"mounted={image.mounted} valid_backup={result.valid_backup_exists}"
    )

    if not result.recovery_image_found:
        errors.append("RECOVERY_IMAGE partition not found")
    elif not image.mounted:
        errors.append("RECOVERY_IMAGE mount failed or unavailable")
    elif image.errors:
        for err in image.errors:
            if err not in errors:
                errors.append(err)

    if not result.valid_backup_exists:
        errors.append("valid backup does not exist")

    if not result.manifest_exists:
        errors.append("manifests/recovery-manifest.json missing")
        restore_preflight_log("manifest validation: missing")
    else:
        restore_preflight_log("manifest validation: present")

    if not result.hashes_exist:
        errors.append("required hashes sidecar files missing")
        restore_preflight_log("hashes validation: missing files")
    else:
        restore_preflight_log("hashes validation: files present")

    if not result.system_image_exists:
        errors.append("images/windows_backup.pcl missing")
    if not result.esp_image_exists:
        errors.append("images/efi_backup.pcl missing")

    topology_reason, layout = discover_layout()
    if topology_reason:
        result.unsupported_topology = True
        errors.append(topology_reason)
        restore_preflight_log(f"unsupported topology: {topology_reason}")
    elif layout is not None:
        result.windows_partition_found = True
        result.efi_partition_found = True
        result.windows_partition = layout.windows.path
        result.efi_partition = layout.efi.path
        restore_preflight_log(
            f"Windows partition detection: {result.windows_partition}"
        )
        restore_preflight_log(f"EFI partition detection: {result.efi_partition}")
    else:
        errors.append("Windows and EFI partition discovery failed")
        restore_preflight_log("Windows/EFI partition detection: failed")

    if layout is not None and _detect_bitlocker_skeleton(result.windows_partition):
        result.bitlocker_detected = True
        errors.append("BitLocker detected; restore not allowed")

    recovery_root: Optional[Path] = None
    if image.mounted and image.mount_point is not None:
        recovery_root = image.mount_point
        if _manifest_exists_on_root(recovery_root) and not result.manifest_exists:
            result.manifest_exists = True
            restore_preflight_log("manifest found via alternate path check")

        if recovery_root and result.manifest_exists and result.hashes_exist:
            ok, hash_reason = _run_hash_validation(recovery_root)
            result.sha256_validation_ready = ok
            if not ok and hash_reason:
                errors.append(hash_reason)

    result.errors = errors
    result.warnings = warnings
    result.restore_enabled = False
    result.restore_preflight_ok = len(errors) == 0

    restore_preflight_log(
        f"restore_allowed preflight_ok={result.restore_preflight_ok} "
        f"restore_enabled={result.restore_enabled} errors={len(errors)} warnings={len(warnings)}"
    )
    for err in errors:
        restore_preflight_log(f"error: {err}")
    for warn in warnings:
        restore_preflight_log(f"warning: {warn}")

    return result


def build_preflight_summary(result: RestorePreflightResult) -> str:
    """Human-readable summary for GTK dialog."""
    lines = [
        "Restore preflight result",
        "",
        f"- valid backup: {'yes' if result.valid_backup_exists else 'no'}",
        f"- manifest: {'ok' if result.manifest_exists else 'missing'}",
        f"- hashes: {'ok' if result.hashes_exist else 'missing'}",
        f"- windows_backup.pcl: {'ok' if result.system_image_exists else 'missing'}",
        f"- efi_backup.pcl: {'ok' if result.esp_image_exists else 'missing'}",
        f"- Windows partition: {result.windows_partition or 'not found'}",
        f"- EFI partition: {result.efi_partition or 'not found'}",
        f"- BitLocker: {'detected' if result.bitlocker_detected else 'not detected'}",
        f"- SHA256 validation: {'ready' if result.sha256_validation_ready else 'not ready'}",
        f"- restore preflight OK: {'yes' if result.restore_preflight_ok else 'no'}",
        f"- restore enabled: {'yes' if result.restore_enabled else 'no'}",
        f"- development mode: {'yes' if result.development_mode else 'no'}",
    ]
    if result.errors:
        lines.append("")
        lines.append("errors:")
        for err in result.errors:
            lines.append(f"  - {err}")
    if result.warnings:
        lines.append("")
        lines.append("warnings:")
        for warn in result.warnings:
            lines.append(f"  - {warn}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for recoverix-restore-preflight."""
    _ = argv
    result = run_restore_preflight()
    print(result.to_json())
    return 0 if result.restore_preflight_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

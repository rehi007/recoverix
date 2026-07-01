"""Dry-run restore planning and validation only (no execution, no writes)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backup_engine.backup_planner import DEFAULT_EXPECTED_MOUNT, GPT_METADATA_RELATIVE, discover_layout
from backup_engine.backup_state import DEFAULT_IMAGE_FILES, WINDOWS_USED_DOMAIN_RELATIVE
from backup_engine.manifest import MANIFEST_FILENAME, DiskMetadata, load_recovery_manifest
from backup_engine.run_backup import build_disk_metadata
from boot_manager.boot_entry import BootEntry, FirmwareAnalysisResult
from boot_manager.bootorder_planner import BootOrderPlan, plan_bootorder_recovery
from common.command import run_readonly
from common.logger import get_logger, setup_logging
from recovery_runtime.discover import require_linux
from partition_manager.models import LABEL_RECOVERY_IMAGE, LABEL_RECOVERY_LINUX
from restore_engine.partclone_restore import (
    BOOTMGFW_EFI_PATH,
    format_gpt_load_command,
    format_gpt_snapshot_command,
    format_partclone_fat_restore_command,
    format_partclone_ntfs_restore_command,
)
from validation.image_validation import (
    RestoreValidationResult,
    validate_restore,
    validate_restore_compatible,
    validate_restore_compatible_quick,
    validate_restore_quick,
)

logger = get_logger(__name__)

_APPLY_FORBIDDEN_MSG = (
    "restore_planner does not execute restore. --apply is forbidden in step 12."
)

ROLLBACK_GPT_RELATIVE = Path("rollback/gpt_pre_restore.bin")
COMPATIBLE_DISK_SIZE_TOLERANCE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class RestoreCheckResult:
    """Single read-only verification step."""

    name: str
    passed: bool
    status: str
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RestorePlan:
    """Dry-run restore simulation result (never executes destructive operations)."""

    status: str
    dry_run: bool
    simulation_only: bool
    execution_allowed: bool
    restore_allowed: bool
    restore_disabled: bool
    restore_mode: str = "disabled"
    compatible_restore: bool = False
    reason: Optional[str] = None
    failure_reasons: List[str] = field(default_factory=list)
    target_disk: Dict[str, Any] = field(default_factory=dict)
    target_partitions: Dict[str, Any] = field(default_factory=dict)
    recovery_image: Dict[str, Any] = field(default_factory=dict)
    validation: Dict[str, Any] = field(default_factory=dict)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    planned_commands: Dict[str, str] = field(default_factory=dict)
    planned_efi_operations: List[str] = field(default_factory=list)
    planned_gpt_rollback: Dict[str, Any] = field(default_factory=dict)
    planned_bootorder_repair: Dict[str, Any] = field(default_factory=dict)
    planned_operations: List[str] = field(default_factory=list)
    compatibility: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _volume_dict(vol) -> Dict[str, Any]:
    return vol.to_dict() if vol is not None else {}


def _disk_dict(disk: DiskMetadata) -> Dict[str, Any]:
    return disk.to_dict()


def _check(
    name: str,
    passed: bool,
    *,
    status: str,
    reason: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> RestoreCheckResult:
    return RestoreCheckResult(
        name=name,
        passed=passed,
        status=status,
        reason=reason,
        details=details or {},
    )


def _find_efi_mount(efi_path: str) -> Optional[Path]:
    """Return existing read-only mount point for the EFI partition if mounted."""
    result = run_readonly(["findmnt", "-n", "-o", "TARGET", efi_path])
    if result.returncode != 0:
        return None
    target = result.stdout.strip()
    return Path(target) if target else None


def verify_recovery_image_accessible(layout) -> RestoreCheckResult:
    recovery = layout.recovery_image
    mount = recovery.mountpoint
    details = {
        "partition": recovery.path,
        "mountpoint": mount,
        "label": recovery.label,
    }
    if not mount:
        return _check(
            "recovery_image_mount",
            False,
            status="MOUNT_REQUIRED",
            reason="RECOVERY_IMAGE must be mounted read-only for manifest validation",
            details=details,
        )
    manifest_candidates = [
        Path(mount) / "manifests" / MANIFEST_FILENAME,
        Path(mount) / "metadata" / MANIFEST_FILENAME,
        Path(mount) / MANIFEST_FILENAME,
    ]
    manifest_path = next((p for p in manifest_candidates if p.is_file()), None)
    details["manifest_path"] = str(manifest_path) if manifest_path else None
    details["manifest_exists"] = manifest_path is not None
    if manifest_path is None:
        return _check(
            "recovery_image_mount",
            False,
            status="FAIL",
            reason="recovery manifest not found on mounted recovery image",
            details=details,
        )
    return _check(
        "recovery_image_mount",
        True,
        status="PASS",
        details=details,
    )


def verify_target_disk(layout, manifest: Dict[str, Any], disk: DiskMetadata) -> RestoreCheckResult:
    manifest_disk = manifest.get("disk_guid")
    manifest_windows = manifest.get("windows_partition_uuid")
    manifest_efi = manifest.get("efi_partition_uuid")
    details = {
        "manifest_disk_guid": manifest_disk,
        "current_disk_guid": disk.disk_guid,
        "manifest_windows_uuid": manifest_windows,
        "current_windows_uuid": disk.windows_partition_uuid,
        "manifest_efi_uuid": manifest_efi,
        "current_efi_uuid": disk.efi_partition_uuid,
        "disk_path": layout.disk_path,
    }
    mismatches: List[str] = []
    if manifest_disk and manifest_disk != disk.disk_guid:
        mismatches.append("disk_guid")
    if manifest_windows and manifest_windows != disk.windows_partition_uuid:
        mismatches.append("windows_partition_uuid")
    if manifest_efi and manifest_efi != disk.efi_partition_uuid:
        mismatches.append("efi_partition_uuid")
    if mismatches:
        return _check(
            "target_disk",
            False,
            status="FAIL",
            reason="target disk/partition identifiers do not match manifest",
            details={**details, "mismatches": mismatches},
        )
    return _check("target_disk", True, status="PASS", details=details)


def _manifest_disk_size(manifest: Dict[str, Any]) -> Optional[int]:
    try:
        return int(manifest.get("disk_size"))
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_partclone_info(output: str) -> Dict[str, int]:
    patterns = {
        "source_blocks": r"Device size:\s+.*=\s*(\d+)\s+Blocks",
        "used_blocks": r"Space in use:\s+.*=\s*(\d+)\s+Blocks",
        "block_size": r"Block size:\s*(\d+)\s+Byte",
    }
    parsed: Dict[str, int] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match is None:
            raise ValueError(f"partclone.info output missing {key}")
        parsed[key] = int(match.group(1))
    parsed["source_size_bytes"] = parsed["source_blocks"] * parsed["block_size"]
    parsed["used_bytes"] = parsed["used_blocks"] * parsed["block_size"]
    return parsed


def read_windows_image_geometry(recovery_root: Path) -> RestoreCheckResult:
    image = recovery_root / DEFAULT_IMAGE_FILES["windows"]
    details: Dict[str, Any] = {
        "windows_image": str(image),
    }
    if not image.is_file():
        return _check(
            "windows_image_geometry",
            False,
            status="FAIL",
            reason="Windows partclone image missing",
            details=details,
        )

    result = run_readonly(["partclone.info", "-L", "/dev/null", "-s", str(image)])
    details["partclone_info_rc"] = result.returncode
    if result.returncode != 0:
        return _check(
            "windows_image_geometry",
            False,
            status="FAIL",
            reason="partclone.info failed for Windows image",
            details={**details, "stderr": result.stderr.strip(), "stdout": result.stdout.strip()},
        )
    info_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    try:
        parsed = _parse_partclone_info(info_output)
    except ValueError as exc:
        return _check(
            "windows_image_geometry",
            False,
            status="FAIL",
            reason=str(exc),
            details={**details, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()},
        )
    return _check(
        "windows_image_geometry",
        True,
        status="PASS",
        details={**details, **parsed},
    )


def _parse_domain_last_used_byte(domain_path: Path) -> Optional[int]:
    if not domain_path.is_file():
        return None
    last_used = 0
    try:
        with domain_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split()
                if len(parts) < 3 or parts[-1] != "+":
                    continue
                try:
                    start = int(parts[0], 0)
                    size = int(parts[1], 0)
                except ValueError:
                    continue
                if size <= 0:
                    continue
                last_used = max(last_used, start + size)
    except OSError:
        return None
    return last_used or None


def _check_windows_target_geometry(layout, recovery_root: Path) -> RestoreCheckResult:
    geometry = read_windows_image_geometry(recovery_root)
    if not geometry.passed:
        return geometry

    target_size = _int_or_none(getattr(layout.windows, "size", None)) or 0
    source_size = int(geometry.details["source_size_bytes"])
    required_size = source_size
    used_bytes = int(geometry.details["used_bytes"])
    size_deficit = max(0, required_size - target_size)
    target_smaller = target_size < required_size
    domain_path = recovery_root / WINDOWS_USED_DOMAIN_RELATIVE
    last_used_byte = _parse_domain_last_used_byte(domain_path)
    details: Dict[str, Any] = {
        **geometry.details,
        "target_windows_size_bytes": target_size,
        "target_windows_path": getattr(layout.windows, "path", None),
        "windows_target_smaller": target_smaller,
        "size_deficit_bytes": size_deficit,
        "restore_required_size_bytes": required_size,
        "partclone_restore_margin_bytes": 0,
        "domain_map_path": str(domain_path),
        "domain_map_present": domain_path.is_file(),
        "domain_last_used_byte": last_used_byte,
        "partclone_no_check_required": False,
        "ntfs_post_resize_required": False,
        "legacy_used_range_unknown": False,
    }

    if target_size <= 0:
        return _check(
            "windows_target_geometry",
            False,
            status="FAIL",
            reason="target Windows partition size is unavailable",
            details=details,
        )

    if not target_smaller:
        return _check(
            "windows_target_geometry",
            True,
            status="PASS",
            reason=(
                "target Windows partition is large enough for source NTFS geometry"
            ),
            details=details,
        )

    details["used_data_fits_target"] = (
        last_used_byte <= target_size if last_used_byte is not None else used_bytes <= target_size
    )
    if last_used_byte is not None:
        details["domain_last_used_fits_target"] = last_used_byte <= target_size
    else:
        details["legacy_used_range_unknown"] = True

    return _check(
        "windows_target_geometry",
        False,
        status="FAIL",
        reason=(
            "Restore cannot continue. The target Windows partition is smaller "
            "than the backup image requirement."
        ),
        details=details,
    )


def verify_windows_target_geometry(layout, recovery_root: Path) -> RestoreCheckResult:
    return _check_windows_target_geometry(layout, recovery_root)


def verify_compatible_target_disk(
    layout,
    manifest: Dict[str, Any],
    disk: DiskMetadata,
    *,
    recovery_root: Optional[Path] = None,
) -> RestoreCheckResult:
    manifest_disk = manifest.get("disk_guid")
    manifest_windows = manifest.get("windows_partition_uuid")
    manifest_efi = manifest.get("efi_partition_uuid")
    manifest_size = _manifest_disk_size(manifest)
    recovery_labels = {LABEL_RECOVERY_IMAGE, LABEL_RECOVERY_LINUX}
    recovery_paths = {
        getattr(layout.recovery_image, "path", None),
    }

    details = {
        "manifest_disk_guid": manifest_disk,
        "current_disk_guid": disk.disk_guid,
        "manifest_windows_uuid": manifest_windows,
        "current_windows_uuid": disk.windows_partition_uuid,
        "manifest_efi_uuid": manifest_efi,
        "current_efi_uuid": disk.efi_partition_uuid,
        "manifest_disk_size": manifest_size,
        "current_disk_size": disk.disk_size,
        "size_tolerance_bytes": COMPATIBLE_DISK_SIZE_TOLERANCE_BYTES,
        "disk_path": layout.disk_path,
        "compatible_restore": True,
        "partclone_no_check_required": False,
        "ntfs_post_resize_required": False,
    }

    failures: List[str] = []
    mismatches: List[str] = []
    if manifest_disk and manifest_disk != disk.disk_guid:
        mismatches.append("disk_guid")
    if manifest_windows and manifest_windows != disk.windows_partition_uuid:
        mismatches.append("windows_partition_uuid")
    if manifest_efi and manifest_efi != disk.efi_partition_uuid:
        mismatches.append("efi_partition_uuid")

    for role, volume in (("windows", layout.windows), ("efi", layout.efi)):
        path = getattr(volume, "path", None)
        label = getattr(volume, "label", None)
        details[f"{role}_path"] = path
        details[f"{role}_label"] = label
        if not path or not Path(path).exists():
            failures.append(f"{role} partition device not found")
        if path in recovery_paths or label in recovery_labels:
            failures.append(f"{role} target resolves to recovery partition")

    if manifest_size is None:
        failures.append("manifest disk_size missing or invalid")
    elif disk.disk_size and disk.disk_size + COMPATIBLE_DISK_SIZE_TOLERANCE_BYTES < manifest_size:
        failures.append("current disk is smaller than backup source disk")

    if recovery_root is not None:
        geometry_check = _check_windows_target_geometry(layout, recovery_root)
        details["windows_target_geometry"] = geometry_check.to_dict()
        if geometry_check.passed:
            details.update(
                {
                    "partclone_no_check_required": bool(
                        geometry_check.details.get("partclone_no_check_required")
                    ),
                    "ntfs_post_resize_required": bool(
                        geometry_check.details.get("ntfs_post_resize_required")
                    ),
                    "windows_target_smaller": bool(
                        geometry_check.details.get("windows_target_smaller")
                    ),
                    "size_deficit_bytes": geometry_check.details.get("size_deficit_bytes"),
                    "source_ntfs_size_bytes": geometry_check.details.get("source_size_bytes"),
                    "source_used_bytes": geometry_check.details.get("used_bytes"),
                    "target_windows_size_bytes": geometry_check.details.get(
                        "target_windows_size_bytes"
                    ),
                    "legacy_used_range_unknown": bool(
                        geometry_check.details.get("legacy_used_range_unknown")
                    ),
                    "domain_map_present": bool(
                        geometry_check.details.get("domain_map_present")
                    ),
                    "domain_last_used_byte": geometry_check.details.get(
                        "domain_last_used_byte"
                    ),
                }
            )
        else:
            failures.append(geometry_check.reason or "Windows target geometry rejected")

    if failures:
        return _check(
            "compatible_target_disk",
            False,
            status="FAIL",
            reason="; ".join(failures),
            details={**details, "mismatches": mismatches, "failures": failures},
        )

    return _check(
        "compatible_target_disk",
        True,
        status="COMPATIBLE" if mismatches else "PASS",
        reason=(
            "target disk accepted for compatible restore"
            if mismatches
            else None
        ),
        details={**details, "mismatches": mismatches},
    )


def _is_device_mismatch_validation(validation: RestoreValidationResult) -> bool:
    reason = (validation.reason or "").lower()
    if "device mismatch" in reason or "device_id" in reason:
        return True
    device = (validation.checks or {}).get("device")
    if isinstance(device, dict):
        device_reason = str(device.get("reason") or "").lower()
        return "device mismatch" in device_reason or "device_id" in device_reason
    return False


def _is_identifier_mismatch_check(check: RestoreCheckResult) -> bool:
    reason = (check.reason or "").lower()
    mismatches = check.details.get("mismatches") if isinstance(check.details, dict) else None
    return (
        check.name == "target_disk"
        and not check.passed
        and bool(mismatches)
        and "identifiers do not match" in reason
    )


def verify_efi_partition_exists(layout) -> RestoreCheckResult:
    efi = layout.efi
    details = _volume_dict(efi)
    device = Path(efi.path)
    if not device.exists():
        return _check(
            "efi_partition",
            False,
            status="FAIL",
            reason="EFI System Partition device not found",
            details=details,
        )
    return _check("efi_partition", True, status="PASS", details=details)


def verify_windows_boot_manager(layout) -> RestoreCheckResult:
    """
  Verify Windows Boot Manager presence via bootmgfw.efi on the ESP.

  Uses an existing read-only mount when available; otherwise records a planned
  verification step (no mount/write in step 12).
  """
    efi = layout.efi
    efi_mount = _find_efi_mount(efi.path)
    bootmgfw = (efi_mount / BOOTMGFW_EFI_PATH) if efi_mount else None
    details = {
        "efi_partition": efi.path,
        "efi_mount": str(efi_mount) if efi_mount else None,
        "bootmgfw_path": str(bootmgfw) if bootmgfw else str(Path(efi.path) / BOOTMGFW_EFI_PATH),
    }
    if efi_mount and bootmgfw:
        try:
            bootmgfw_exists = bootmgfw.is_file()
        except OSError as exc:
            return _check(
                "windows_boot_manager",
                True,
                status="PLANNED",
                reason=f"could not read bootmgfw.efi ({exc}); planned read-only verification",
                details=details,
            )
        if bootmgfw_exists:
            return _check(
                "windows_boot_manager",
                True,
                status="PASS",
                reason=None,
                details={**details, "verified_via": "bootmgfw.efi on mounted ESP"},
            )
        return _check(
            "windows_boot_manager",
            False,
            status="FAIL",
            reason="EFI/Microsoft/Boot/bootmgfw.efi not found on mounted ESP",
            details=details,
        )
    return _check(
        "windows_boot_manager",
        True,
        status="PLANNED",
        reason="ESP not mounted; planned read-only verification of bootmgfw.efi",
        details={**details, "planned_verification": f"mount {efi.path} ro && test -f {BOOTMGFW_EFI_PATH}"},
    )


def _simulated_firmware_analysis() -> FirmwareAnalysisResult:
    """Linux simulation for BootOrder repair planning (no bcdedit execution)."""
    return FirmwareAnalysisResult(
        windows_boot_manager=BootEntry(
            identifier="{simulated-windows-bootmgr}",
            description="Windows Boot Manager",
            path=r"\EFI\Microsoft\Boot\bootmgfw.efi",
        ),
        recovery_boot=BootEntry(
            identifier="{simulated-recoveryboot}",
            description="RecoveryBoot",
            path=r"\EFI\RecoveryBoot\shimx64.efi",
        ),
        boot_order=["{simulated-recoveryboot}", "{simulated-windows-bootmgr}"],
        boot_next=None,
        entries=[],
        status="SIMULATED",
        dry_run=True,
    )


def build_planned_commands(
    *,
    layout,
    recovery_root: Path,
) -> Dict[str, str]:
    gpt_image = recovery_root / DEFAULT_IMAGE_FILES["gpt"]
    efi_image = recovery_root / DEFAULT_IMAGE_FILES["efi"]
    windows_image = recovery_root / DEFAULT_IMAGE_FILES["windows"]
    return {
        "gpt_restore": format_gpt_load_command(gpt_image, layout.disk_path),
        "efi_partclone_restore": format_partclone_fat_restore_command(
            efi_image,
            layout.efi.path,
        ),
        "windows_partclone_restore": format_partclone_ntfs_restore_command(
            windows_image,
            layout.windows.path,
        ),
        "gpt_pre_restore_snapshot": format_gpt_snapshot_command(
            recovery_root / ROLLBACK_GPT_RELATIVE,
            layout.disk_path,
        ),
    }


def build_planned_efi_operations() -> List[str]:
    return [
        "simulation only: no mount write, no EFI overwrite in step 12",
        "planned: restore ESP from images/efi_backup.pcl via partclone.fat (target partition)",
        f"policy: never overwrite {BOOTMGFW_EFI_PATH}",
        "planned: verify bootmgfw.efi remains intact after EFI restore",
        "planned: restore RecoveryBoot shimx64/grubx64 from backup assets if missing",
    ]


def build_gpt_rollback_plan(*, layout, recovery_root: Path) -> Dict[str, Any]:
    gpt_backup = recovery_root / GPT_METADATA_RELATIVE
    pre_snapshot = recovery_root / ROLLBACK_GPT_RELATIVE
    return {
        "simulation_only": True,
        "execution_allowed": False,
        "pre_restore_snapshot": format_gpt_snapshot_command(pre_snapshot, layout.disk_path),
        "rollback_from_manifest_gpt": format_gpt_load_command(gpt_backup, layout.disk_path),
        "rollback_artifact": str(gpt_backup),
        "pre_restore_snapshot_path": str(pre_snapshot),
        "planned_operations": [
            "capture current GPT to rollback/gpt_pre_restore.bin before any restore",
            "on failure: load manifest GPT backup via sgdisk --load-backup",
            "step 12: planning only; no GPT write",
        ],
    }


def _firmware_analysis_for_plan(*, live: bool) -> FirmwareAnalysisResult:
    if live and sys.platform == "win32":
        from boot_manager.firmware_reader import read_firmware_boot

        return read_firmware_boot(dry_run=True)
    return _simulated_firmware_analysis()


def build_restore_plan(*, live: bool = True, fast_validation: bool = False) -> RestorePlan:
    """Discover environment, validate backup, and build a dry-run restore plan."""
    require_linux()
    failure_reasons: List[str] = []
    checks: List[RestoreCheckResult] = []
    planned_operations = [
        "restore simulation only (step 12)",
        "read-only discovery and validation",
        "no partclone restore execution",
        "no GPT write",
        "no EFI overwrite",
        "no BootOrder modification",
        "no partition format",
        "no mount write",
    ]

    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        failure_reasons.append(topology_reason or "layout discovery failed")
        return RestorePlan(
            status="REJECTED",
            dry_run=True,
            simulation_only=True,
            execution_allowed=False,
            restore_allowed=False,
            restore_disabled=True,
            reason=topology_reason,
            failure_reasons=failure_reasons,
            planned_operations=planned_operations,
            checks=[c.to_dict() for c in checks],
        )

    recovery_access = verify_recovery_image_accessible(layout)
    checks.append(recovery_access)
    if not recovery_access.passed:
        failure_reasons.append(recovery_access.reason or recovery_access.status)

    recovery_mount = Path(
        layout.recovery_image.mountpoint or DEFAULT_EXPECTED_MOUNT
    )

    validation = RestoreValidationResult(
        allowed=False,
        status="SKIPPED",
        reason="recovery image not accessible",
    )
    manifest: Dict[str, Any] = {}
    target_ok = False
    compatible_restore = False
    compatibility: Dict[str, Any] = {}
    disk = DiskMetadata(
        disk_guid="unknown",
        disk_model="unknown",
        disk_serial="unknown",
        disk_size=0,
        windows_partition_uuid="unknown",
        efi_partition_uuid="unknown",
    )

    if recovery_access.passed:
        disk = build_disk_metadata(layout)
        try:
            validator = validate_restore_quick if fast_validation else validate_restore
            validation = validator(recovery_mount, disk)
        except Exception as exc:
            logger.exception("restore validation exception")
            validation = RestoreValidationResult(
                allowed=False,
                status="REJECTED",
                reason="validation_exception",
                checks={"error": str(exc)},
            )
            failure_reasons.append("validation_exception")
        else:
            if not validation.allowed and _is_device_mismatch_validation(validation):
                compatible_validator = (
                    validate_restore_compatible_quick
                    if fast_validation
                    else validate_restore_compatible
                )
                compatible_validation = compatible_validator(recovery_mount, disk)
                compatibility["validation"] = compatible_validation.to_dict()
                if compatible_validation.allowed:
                    validation = compatible_validation
                    compatible_restore = True
                else:
                    failure_reasons.append(
                        compatible_validation.reason or compatible_validation.status
                    )
            elif not validation.allowed:
                failure_reasons.append(validation.reason or validation.status)

            if validation.allowed:
                manifest = load_recovery_manifest(recovery_mount)
                target_disk_check = verify_target_disk(layout, manifest, disk)
                checks.append(target_disk_check)
                if target_disk_check.passed:
                    geometry_check = verify_windows_target_geometry(
                        layout,
                        recovery_mount,
                    )
                    checks.append(geometry_check)
                    if geometry_check.passed:
                        target_ok = True
                    else:
                        failure_reasons.append(
                            geometry_check.reason or "Windows target geometry rejected"
                        )
                elif _is_identifier_mismatch_check(target_disk_check):
                    compatible_target = verify_compatible_target_disk(
                        layout,
                        manifest,
                        disk,
                        recovery_root=recovery_mount,
                    )
                    checks.append(compatible_target)
                    compatibility["target_disk"] = compatible_target.to_dict()
                    if compatible_target.passed:
                        target_ok = True
                        compatible_restore = True
                    else:
                        failure_reasons.append(
                            compatible_target.reason or "compatible target disk rejected"
                        )
                else:
                    failure_reasons.append(
                        target_disk_check.reason or "target_disk mismatch"
                    )

    efi_check = verify_efi_partition_exists(layout)
    checks.append(efi_check)
    if not efi_check.passed:
        failure_reasons.append(efi_check.reason or "efi_partition missing")

    bootmgr_check = verify_windows_boot_manager(layout)
    checks.append(bootmgr_check)
    if bootmgr_check.status == "FAIL":
        failure_reasons.append(bootmgr_check.reason or "windows_boot_manager missing")

    restore_allowed = (
        recovery_access.passed
        and validation.allowed
        and efi_check.passed
        and bootmgr_check.status != "FAIL"
        and target_ok
    )

    planned_commands: Dict[str, str] = {}
    planned_efi_ops: List[str] = build_planned_efi_operations()
    gpt_rollback: Dict[str, Any] = {}
    bootorder_plan: Dict[str, Any] = {}

    if recovery_access.passed:
        planned_commands = build_planned_commands(
            layout=layout,
            recovery_root=recovery_mount,
        )
        gpt_rollback = build_gpt_rollback_plan(
            layout=layout,
            recovery_root=recovery_mount,
        )
        boot_plan = plan_bootorder_recovery(
            _firmware_analysis_for_plan(live=live),
            dry_run=True,
        )
        bootorder_plan = boot_plan.to_dict()
        planned_operations.extend(
            [
                f"planned GPT restore: {planned_commands.get('gpt_restore', '')}",
                f"planned EFI restore: {planned_commands.get('efi_partclone_restore', '')}",
                f"planned Windows restore: {planned_commands.get('windows_partclone_restore', '')}",
            ]
        )
        planned_operations.extend(boot_plan.planned_actions)

    status = "PLANNED" if restore_allowed else "REJECTED"
    if recovery_access.status == "MOUNT_REQUIRED":
        status = "MOUNT_REQUIRED"

    return RestorePlan(
        status=status,
        dry_run=True,
        simulation_only=True,
        execution_allowed=False,
        restore_allowed=restore_allowed,
        restore_disabled=not restore_allowed,
        restore_mode="compatible" if restore_allowed and compatible_restore else ("standard" if restore_allowed else "disabled"),
        compatible_restore=bool(restore_allowed and compatible_restore),
        reason=None if restore_allowed else "; ".join(failure_reasons) or "restore not allowed",
        failure_reasons=failure_reasons,
        target_disk={
            "disk_path": layout.disk_path,
            "metadata": _disk_dict(disk),
        },
        target_partitions={
            "efi": _volume_dict(layout.efi),
            "windows": _volume_dict(layout.windows),
        },
        recovery_image=_volume_dict(layout.recovery_image),
        validation=validation.to_dict(),
        checks=[c.to_dict() for c in checks],
        planned_commands=planned_commands,
        planned_efi_operations=planned_efi_ops,
        planned_gpt_rollback=gpt_rollback,
        planned_bootorder_repair=bootorder_plan,
        planned_operations=planned_operations,
        compatibility=compatibility,
    )


def create_restore_plan(*, live: bool = True) -> RestorePlan:
    return build_restore_plan(live=live)


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Generate dry-run restore plan only (no execution)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate restore planning and validation (required)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument("--apply", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.apply:
        print(_APPLY_FORBIDDEN_MSG, file=sys.stderr)
        return 2

    if not args.dry_run:
        parser.error("--dry-run is required for restore_planner")

    plan = create_restore_plan()
    print(plan.to_json(indent=2 if args.json else None))
    return 0 if plan.status == "PLANNED" else 1


if __name__ == "__main__":
    raise SystemExit(main())

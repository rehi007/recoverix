"""Dry-run restore planning and validation only (no execution, no writes)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backup_engine.backup_planner import DEFAULT_EXPECTED_MOUNT, GPT_METADATA_RELATIVE, discover_layout
from backup_engine.backup_state import DEFAULT_IMAGE_FILES
from backup_engine.manifest import MANIFEST_FILENAME, DiskMetadata, load_recovery_manifest
from backup_engine.run_backup import build_disk_metadata
from boot_manager.boot_entry import BootEntry, FirmwareAnalysisResult
from boot_manager.bootorder_planner import BootOrderPlan, plan_bootorder_recovery
from common.command import run_readonly
from common.logger import get_logger, setup_logging
from recovery_runtime.discover import require_linux
from restore_engine.partclone_restore import (
    BOOTMGFW_EFI_PATH,
    format_gpt_load_command,
    format_gpt_snapshot_command,
    format_partclone_fat_restore_command,
    format_partclone_ntfs_restore_command,
)
from validation.image_validation import RestoreValidationResult, validate_restore

logger = get_logger(__name__)

_APPLY_FORBIDDEN_MSG = (
    "restore_planner does not execute restore. --apply is forbidden in step 12."
)

ROLLBACK_GPT_RELATIVE = Path("rollback/gpt_pre_restore.bin")


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
    manifest_path = Path(mount) / MANIFEST_FILENAME
    details["manifest_path"] = str(manifest_path)
    details["manifest_exists"] = manifest_path.is_file()
    if not manifest_path.is_file():
        return _check(
            "recovery_image_mount",
            False,
            status="FAIL",
            reason=f"{MANIFEST_FILENAME} not found on mounted recovery image",
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
        f"planned: restore ESP from images/efi.pcl via partclone.fat (target partition)",
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


def build_restore_plan(*, live: bool = True) -> RestorePlan:
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
            validation = validate_restore(recovery_mount, disk)
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
            if not validation.allowed:
                failure_reasons.append(validation.reason or validation.status)
            else:
                manifest = load_recovery_manifest(recovery_mount)
                target_disk_check = verify_target_disk(layout, manifest, disk)
                checks.append(target_disk_check)
                if not target_disk_check.passed:
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

    target_ok = all(c.passed for c in checks if c.name == "target_disk")
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

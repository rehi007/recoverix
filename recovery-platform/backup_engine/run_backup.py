"""Execute or plan full backup (dry-run default, --apply for writes)."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backup_engine.backup_state import (
    DEFAULT_IMAGE_FILES,
    has_incomplete_backup,
    mark_incomplete_backup,
)
from backup_engine.backup_planner import (
    DEFAULT_EXPECTED_MOUNT,
    EFI_IMAGE_RELATIVE,
    GPT_METADATA_RELATIVE,
    IMAGE_RELATIVE,
    discover_layout,
    has_valid_backup,
    read_bitlocker_state,
)
from backup_engine.manifest import (
    DiskMetadata,
    ManifestContext,
    finalize_backup_manifest,
)
from backup_engine.partclone_wrapper import (
    format_gpt_backup_command,
    format_partclone_fat_command,
    format_partclone_ntfs_command,
)
from backup_engine.space_estimation import (
    GPT_OVERHEAD_BYTES,
    BackupSizeEstimate,
    estimate_backup_space,
    estimate_required_bytes,
)
from backup_engine.write_guard import WriteGuard, WriteForbiddenError
from common.command import run_command, run_readonly
from common.errors import ConfirmationRequiredError
from common.logger import get_logger, setup_logging
from recovery_runtime.discover import require_linux
from recovery_runtime.mounts import mount_point_for_label, plan_mount

logger = get_logger(__name__)

@dataclass(frozen=True)
class BackupRunResult:
    """Dry-run plan or apply execution result."""

    status: str
    dry_run: bool
    apply: bool
    confirmed: bool
    can_backup: bool
    reason: Optional[str] = None
    backup_targets: Dict[str, Any] = field(default_factory=dict)
    expected_image_paths: Dict[str, str] = field(default_factory=dict)
    estimated_required_bytes: int = 0
    estimated_required_gb: float = 0.0
    estimated_used_bytes: int = 0
    estimation_method: str = ""
    estimation_warning: Optional[str] = None
    recovery_image_free_bytes: Optional[int] = None
    estimation_details: Dict[str, Any] = field(default_factory=dict)
    planned_commands: Dict[str, str] = field(default_factory=dict)
    expected_manifest: Dict[str, Any] = field(default_factory=dict)
    planned_operations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _probe_value(device: str, key: str) -> Optional[str]:
    result = run_readonly(["blkid", "-o", "value", "-s", key, device])
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def build_disk_metadata(layout) -> DiskMetadata:
    disk_guid = _probe_value(layout.disk_path, "UUID") or f"disk:{layout.disk_path}"
    return DiskMetadata(
        disk_guid=disk_guid if disk_guid.startswith("{") else f"{{{disk_guid}}}",
        disk_model=_probe_value(layout.disk_path, "MODEL") or "unknown",
        disk_serial=_probe_value(layout.disk_path, "SERIAL") or "unknown",
        disk_size=sum(
            size
            for size in (
                layout.windows.size,
                layout.efi.size,
                layout.recovery_image.size,
            )
            if size
        )
        or 0,
        windows_partition_uuid=_format_uuid(_probe_value(layout.windows.path, "PARTUUID"), layout.windows.path),
        efi_partition_uuid=_format_uuid(_probe_value(layout.efi.path, "PARTUUID"), layout.efi.path),
    )


def _format_uuid(value: Optional[str], device: str) -> str:
    if not value:
        return f"unknown:{device}"
    return value if value.startswith("{") else f"{{{value}}}"


def _result_from_estimate(
    estimate: BackupSizeEstimate,
    *,
    base: BackupRunResult,
) -> BackupRunResult:
    """Merge space estimate into an existing result; block backup if space insufficient."""
    can_backup = base.can_backup and estimate.can_backup
    reason = base.reason
    if estimate.estimation_warning and estimate.estimation_method == "partition_size_fallback":
        reason = (
            estimate.estimation_warning
            if not reason
            else f"{reason}; {estimate.estimation_warning}"
        )
    elif estimate.reason and not reason:
        reason = estimate.reason
    status = base.status
    if base.can_backup and not estimate.can_backup:
        reason = estimate.reason or "insufficient recovery image space"
        if status == "PLANNED":
            status = "REJECTED"
    return BackupRunResult(
        status=status,
        dry_run=base.dry_run,
        apply=base.apply,
        confirmed=base.confirmed,
        can_backup=can_backup,
        reason=reason,
        backup_targets=base.backup_targets,
        expected_image_paths=base.expected_image_paths,
        estimated_required_bytes=estimate.estimated_required_bytes,
        estimated_required_gb=estimate.estimated_required_gb,
        estimated_used_bytes=estimate.estimated_used_bytes,
        estimation_method=estimate.estimation_method,
        estimation_warning=estimate.estimation_warning,
        recovery_image_free_bytes=estimate.recovery_image_free_bytes,
        estimation_details=estimate.estimation_details,
        planned_commands=base.planned_commands,
        expected_manifest=base.expected_manifest,
        planned_operations=base.planned_operations,
    )


def build_expected_manifest(
    *,
    layout,
    recovery_mount: Path,
    disk: DiskMetadata,
) -> Dict[str, Any]:
    image_files = dict(DEFAULT_IMAGE_FILES)
    return {
        "image_version": "1",
        "created_at": "<set-on-finalize>",
        "disk_guid": disk.disk_guid,
        "disk_model": disk.disk_model,
        "disk_serial": disk.disk_serial,
        "disk_size": disk.disk_size,
        "windows_partition_uuid": disk.windows_partition_uuid,
        "efi_partition_uuid": disk.efi_partition_uuid,
        "image_files": image_files,
        "sha256_hashes": {path: "<computed-after-backup>" for path in image_files.values()},
        "tool_versions": {"partclone": "unknown", "recovery_platform": "0.1.0"},
        "device_id": ManifestContext(recovery_root=recovery_mount, disk=disk).device_id(),
        "backup_complete": False,
    }


def _resolve_recovery_mount(layout, guard: WriteGuard) -> tuple[Path, List[str]]:
    operations: List[str] = []
    if layout.recovery_image.mountpoint:
        mount = Path(layout.recovery_image.mountpoint)
        operations.append(f"use existing mount: {mount}")
        return mount, operations

    planned_mount = mount_point_for_label("RECOVERY_IMAGE")
    operations.append(
        plan_mount(layout.recovery_image.path, planned_mount, read_only=False)
    )
    if guard.dry_run:
        operations.append(f"planned mount target: {planned_mount}")
        return planned_mount, operations

    guard.mkdir(planned_mount.parent, operation="mount-parent-mkdir")
    result = run_command(
        ["mount", "-o", "rw", layout.recovery_image.path, str(planned_mount)],
        dry_run=False,
        confirmed=guard.confirmed,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mount failed: {result.stderr.strip()}")
    operations.append(f"mounted {layout.recovery_image.path} -> {planned_mount}")
    return planned_mount, operations


def plan_backup_run(guard: WriteGuard) -> BackupRunResult:
    """Build dry-run backup plan without any writes."""
    require_linux()
    bitlocker = read_bitlocker_state(live=True)
    if bitlocker == "ON":
        return BackupRunResult(
            status="REJECTED",
            dry_run=guard.dry_run,
            apply=guard.apply,
            confirmed=guard.confirmed,
            can_backup=False,
            reason="BitLocker is ON; backup refused",
        )

    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        return BackupRunResult(
            status="REJECTED",
            dry_run=guard.dry_run,
            apply=guard.apply,
            confirmed=guard.confirmed,
            can_backup=False,
            reason=topology_reason or "layout discovery failed",
        )

    recovery_mount = (
        Path(layout.recovery_image.mountpoint)
        if layout.recovery_image.mountpoint
        else mount_point_for_label("RECOVERY_IMAGE")
    )
    disk = build_disk_metadata(layout)
    estimate = estimate_backup_space(layout)
    logger.info(
        "backup dry-run space: method=%s used=%s required_gb=%s warning=%s",
        estimate.estimation_method,
        estimate.estimated_used_bytes,
        estimate.estimated_required_gb,
        estimate.estimation_warning,
    )

    image_paths = {
        "gpt": str(recovery_mount / GPT_METADATA_RELATIVE),
        "efi": str(recovery_mount / EFI_IMAGE_RELATIVE),
        "windows": str(recovery_mount / IMAGE_RELATIVE),
    }
    commands = {
        "gpt_backup": format_gpt_backup_command(
            layout.disk_path,
            recovery_mount / GPT_METADATA_RELATIVE,
        ),
        "efi_backup": format_partclone_fat_command(
            layout.efi.path,
            recovery_mount / EFI_IMAGE_RELATIVE,
        ),
        "windows_partclone": format_partclone_ntfs_command(
            layout.windows.path,
            recovery_mount / IMAGE_RELATIVE,
        ),
    }

    planned_ops = [
        "discover backup targets (read-only)",
        plan_mount(
            layout.recovery_image.path,
            recovery_mount,
            read_only=False,
        )
        if not layout.recovery_image.mountpoint
        else f"use mounted RECOVERY_IMAGE at {recovery_mount}",
        f"planned GPT backup: {commands['gpt_backup']}",
        f"planned EFI backup: {commands['efi_backup']}",
        f"planned Windows backup: {commands['windows_partclone']}",
        "planned SHA256 hash generation",
        "planned recovery-manifest.json finalize (last step)",
        (
            f"space estimate: method={estimate.estimation_method} "
            f"windows_used={estimate.estimated_used_bytes} "
            f"required_gb={estimate.estimated_required_gb}"
        ),
    ]
    if estimate.estimation_warning:
        planned_ops.append(f"space estimate warning: {estimate.estimation_warning}")
    if estimate.reason and estimate.estimation_method == "partition_size_fallback":
        planned_ops.append(f"space estimate note: {estimate.reason}")

    if has_valid_backup(recovery_mount) and layout.recovery_image.mountpoint:
        blocked = BackupRunResult(
            status="BLOCKED",
            dry_run=guard.dry_run,
            apply=guard.apply,
            confirmed=guard.confirmed,
            can_backup=False,
            reason="valid backup already exists",
            backup_targets=_targets_dict(layout),
            expected_image_paths=image_paths,
            planned_commands=commands,
            expected_manifest=build_expected_manifest(
                layout=layout,
                recovery_mount=recovery_mount,
                disk=disk,
            ),
            planned_operations=planned_ops,
        )
        return _result_from_estimate(estimate, base=blocked)

    planned = BackupRunResult(
        status="PLANNED",
        dry_run=guard.dry_run,
        apply=guard.apply,
        confirmed=guard.confirmed,
        can_backup=True,
        backup_targets=_targets_dict(layout),
        expected_image_paths=image_paths,
        planned_commands=commands,
        expected_manifest=build_expected_manifest(
            layout=layout,
            recovery_mount=recovery_mount,
            disk=disk,
        ),
        planned_operations=planned_ops,
    )
    return _result_from_estimate(estimate, base=planned)


def _targets_dict(layout) -> Dict[str, Any]:
    return {
        "disk": layout.disk_path,
        "efi_partition": layout.efi.path,
        "windows_partition": layout.windows.path,
        "recovery_image_partition": layout.recovery_image.path,
    }


def execute_backup_apply(guard: WriteGuard) -> BackupRunResult:
    """Execute backup writes (requires --apply --confirm)."""
    plan = plan_backup_run(guard)
    if not plan.can_backup:
        return plan

    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        return BackupRunResult(
            status="FAILED",
            dry_run=False,
            apply=True,
            confirmed=guard.confirmed,
            can_backup=False,
            reason=topology_reason,
        )

    operations: List[str] = list(plan.planned_operations)
    try:
        recovery_mount, mount_ops = _resolve_recovery_mount(layout, guard)
        operations.extend(mount_ops)

        if has_valid_backup(recovery_mount):
            return BackupRunResult(
                status="BLOCKED",
                dry_run=False,
                apply=True,
                confirmed=guard.confirmed,
                can_backup=False,
                reason="valid backup already exists",
                planned_operations=operations,
            )

        gpt_out = recovery_mount / GPT_METADATA_RELATIVE
        efi_out = recovery_mount / EFI_IMAGE_RELATIVE
        windows_out = recovery_mount / IMAGE_RELATIVE

        guard.mkdir(gpt_out.parent, operation="artifact-dir")
        guard.mkdir(efi_out.parent, operation="artifact-dir")

        gpt_cmd = format_gpt_backup_command(layout.disk_path, gpt_out)
        efi_cmd = format_partclone_fat_command(layout.efi.path, efi_out)
        win_cmd = format_partclone_ntfs_command(layout.windows.path, windows_out)

        for cmd in (gpt_cmd, efi_cmd, win_cmd):
            argv = shlex.split(cmd)
            result = run_command(argv, dry_run=False, confirmed=guard.confirmed)
            if result.returncode != 0:
                raise RuntimeError(f"command failed: {cmd}")

        disk = build_disk_metadata(layout)
        ctx = ManifestContext(recovery_root=recovery_mount, disk=disk)
        finalize_backup_manifest(ctx)
        operations.append("finalized recovery-manifest.json")

        return BackupRunResult(
            status="COMPLETED",
            dry_run=False,
            apply=True,
            confirmed=guard.confirmed,
            can_backup=True,
            backup_targets=_targets_dict(layout),
            expected_image_paths=plan.expected_image_paths,
            estimated_required_bytes=plan.estimated_required_bytes,
            estimated_required_gb=plan.estimated_required_gb,
            estimated_used_bytes=plan.estimated_used_bytes,
            estimation_method=plan.estimation_method,
            estimation_warning=plan.estimation_warning,
            recovery_image_free_bytes=plan.recovery_image_free_bytes,
            estimation_details=plan.estimation_details,
            planned_commands=plan.planned_commands,
            expected_manifest=plan.expected_manifest,
            planned_operations=operations,
        )
    except (ConfirmationRequiredError, WriteForbiddenError):
        raise
    except Exception as exc:
        logger.exception("backup apply failed")
        mark_incomplete_backup(recovery_mount if "recovery_mount" in locals() else Path(DEFAULT_EXPECTED_MOUNT), str(exc))
        return BackupRunResult(
            status="FAILED",
            dry_run=False,
            apply=True,
            confirmed=guard.confirmed,
            can_backup=False,
            reason=str(exc),
            planned_operations=operations,
        )


def run_backup(*, apply: bool, confirmed: bool) -> BackupRunResult:
    guard = WriteGuard(apply=apply, confirmed=confirmed)
    if guard.dry_run:
        return plan_backup_run(guard)
    return execute_backup_apply(guard)


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Run full backup (dry-run default)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Plan only (no writes)")
    mode.add_argument("--apply", action="store_true", help="Execute backup writes")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required with --apply to authorize writes",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.apply and not args.confirm:
        print(
            "error: --apply requires --confirm to authorize writes",
            file=sys.stderr,
        )
        return 2

    try:
        result = run_backup(apply=args.apply, confirmed=args.confirm)
    except (WriteForbiddenError, ConfirmationRequiredError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(result.to_json(indent=2 if args.json else None))
    if result.status in ("PLANNED", "COMPLETED"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

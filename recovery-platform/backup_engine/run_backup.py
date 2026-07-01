"""Execute or plan full backup (dry-run default, --apply for writes)."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from backup_engine.backup_state import (
    DEFAULT_IMAGE_FILES,
    WINDOWS_USED_DOMAIN_RELATIVE,
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
from backup_engine.backup_artifacts import verify_artifact_file
from backup_engine.backup_finalize import finalize_log
from backup_engine.backup_runtime_log import runtime_log
from backup_engine.efi_backup import EfiBackupError, run_efi_backup_precheck, verify_efi_artifact
from backup_engine.manifest import (
    DiskMetadata,
    ManifestContext,
    development_mode_enabled,
    finalize_backup_manifest,
)
from backup_engine.partclone_wrapper import (
    format_gpt_backup_command,
    format_partclone_fat_command,
    format_partclone_ntfs_command,
    format_partclone_ntfs_domain_command,
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

ProgressCallback = Callable[[dict[str, Any]], None]

PARTCLONE_PROGRESS_RE = re.compile(r"(?P<percent>\d+(?:\.\d+)?)\s*%")
DEFAULT_BACKUP_THROUGHPUT_BYTES_PER_SEC = 90 * 1024 * 1024
DEFAULT_HASH_THROUGHPUT_BYTES_PER_SEC = 180 * 1024 * 1024
DEFAULT_EFI_THROUGHPUT_BYTES_PER_SEC = 50 * 1024 * 1024
MIN_WINDOWS_BACKUP_SECONDS = 30.0
MIN_EFI_BACKUP_SECONDS = 5.0
MIN_GPT_BACKUP_SECONDS = 2.0
MIN_HASH_SECONDS = 20.0
STANDARD_WINDOWS_PROGRESS_MULTIPLIER = 2.6
COMPACT_WINDOWS_PROGRESS_MULTIPLIER = 1.0

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
    if estimate.estimation_warning:
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
    backup_type: str = "standard",
    admin_backup_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    image_files = dict(DEFAULT_IMAGE_FILES)
    manifest_context = ManifestContext(
        recovery_root=recovery_mount,
        disk=disk,
        backup_type=backup_type,
        restore_baseline_bytes=(
            int(admin_backup_metadata["restore_baseline_bytes"])
            if admin_backup_metadata and admin_backup_metadata.get("restore_baseline_bytes") is not None
            else None
        ),
        source_windows_partition_size_bytes=(
            int(admin_backup_metadata["source_windows_partition_size_bytes"])
            if admin_backup_metadata
            and admin_backup_metadata.get("source_windows_partition_size_bytes") is not None
            else None
        ),
        admin_backup=dict(admin_backup_metadata or {}),
    )
    return {
        "image_version": "1",
        "backup_version": "1",
        "created_at": "<set-on-finalize>",
        "version": 1,
        "backup_type": backup_type,
        "disk_guid": disk.disk_guid,
        "disk_model": disk.disk_model,
        "disk_serial": disk.disk_serial,
        "disk_size": disk.disk_size,
        "windows_partition_uuid": disk.windows_partition_uuid,
        "efi_partition_uuid": disk.efi_partition_uuid,
        "source_partitions": {
            "windows_partition_uuid": disk.windows_partition_uuid,
            "efi_partition_uuid": disk.efi_partition_uuid,
        },
        "image_files": image_files,
        "image_filenames": image_files,
        "windows_image": image_files["windows"],
        "efi_image": image_files["efi"],
        "gpt_backup": image_files["gpt"],
        "hashes": {
            "windows": "hashes/windows_backup.sha256",
            "efi": "hashes/efi_backup.sha256",
            "gpt": "hashes/gpt_backup.sha256",
            "manifest": "hashes/manifest.sha256",
        },
        "sha256_filenames": {
            path: str(Path("hashes") / f"{Path(path).stem}.sha256")
            for path in image_files.values()
        },
        "sha256_hashes": {path: "<computed-after-backup>" for path in image_files.values()},
        "compatibility": {
            "windows_used_domain": WINDOWS_USED_DOMAIN_RELATIVE,
        },
        "tool_versions": {"partclone": "unknown", "recovery_platform": "0.1.0"},
        "device_id": manifest_context.device_id(),
        "backup_complete": False,
        "development_mode": development_mode_enabled(),
        **(
            {
                "restore_baseline_bytes": int(
                    admin_backup_metadata["restore_baseline_bytes"]
                )
            }
            if admin_backup_metadata
            and admin_backup_metadata.get("restore_baseline_bytes") is not None
            else {}
        ),
        **(
            {
                "source_windows_partition_size_bytes": int(
                    admin_backup_metadata["source_windows_partition_size_bytes"]
                )
            }
            if admin_backup_metadata
            and admin_backup_metadata.get("source_windows_partition_size_bytes") is not None
            else {}
        ),
        **({"admin_backup": dict(admin_backup_metadata)} if admin_backup_metadata else {}),
    }


def _resolve_recovery_mount(layout, guard: WriteGuard) -> tuple[Path, List[str]]:
    operations: List[str] = []
    if layout.recovery_image.mountpoint:
        mount = Path(layout.recovery_image.mountpoint)
        if not guard.dry_run and _mount_is_readonly(mount):
            result = run_command(
                ["mount", "-o", "remount,rw", layout.recovery_image.path, str(mount)],
                dry_run=False,
                confirmed=guard.confirmed,
            )
            if result.returncode != 0:
                raise RuntimeError(f"remount rw failed: {result.stderr.strip()}")
            operations.append(f"remounted rw: {mount}")
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


def _command_failure_detail(label: str, command: str, result) -> str:
    stderr = (result.stderr or "").strip()
    stdout = (result.stdout or "").strip()
    detail = stderr or stdout or command
    return f"{label} command failed (exit {result.returncode}): {detail}"


def _mounted_targets_for_device(device_path: str) -> List[str]:
    targets: List[str] = []
    try:
        with Path("/proc/self/mounts").open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device_path:
                    targets.append(parts[1])
    except OSError:
        return []
    return targets


def _emit_progress(
    progress_callback: Optional[ProgressCallback],
    *,
    stage: str,
    step: int,
    total: int,
    message: str,
    percent: Optional[int] = None,
    detail: Optional[str] = None,
    final: bool = False,
) -> None:
    if progress_callback is None:
        return
    payload: dict[str, Any] = {
        "stage": stage,
        "step": step,
        "total": total,
        "message": message,
    }
    if percent is not None:
        payload["percent"] = max(0, min(100, int(percent)))
    if detail:
        payload["detail"] = detail
    if final:
        payload["final"] = True
    progress_callback(payload)


def _extract_partclone_percent(text: str) -> Optional[int]:
    matches = list(PARTCLONE_PROGRESS_RE.finditer(text))
    if not matches:
        return None
    try:
        value = float(matches[-1].group("percent"))
    except ValueError:
        return None
    return max(0, min(100, int(round(value))))


def _estimated_file_percent(path: Path, estimated_bytes: int) -> Optional[int]:
    if estimated_bytes <= 0:
        return None
    try:
        current = path.stat().st_size
    except OSError:
        return None
    if current <= 0:
        return 0
    return max(0, min(99, int((current / estimated_bytes) * 100)))


def _build_backup_progress_ranges(plan: BackupRunResult, layout: Any) -> Dict[str, tuple[int, int]]:
    return {
        "prepare": (0, 2),
        "windows": (2, 52),
        "windows_verify": (52, 53),
        "efi": (53, 54),
        "efi_verify": (54, 55),
        "gpt": (55, 56),
        "gpt_verify": (56, 57),
        "metadata": (57, 58),
        "hash_generation": (58, 80),
        "hash_validation": (80, 97),
        "final_closeout": (97, 99),
    }


def _range_percent(
    ranges: Dict[str, tuple[int, int]],
    stage: str,
    percent: int,
) -> int:
    start, end = ranges[stage]
    span = max(0, end - start)
    bounded = max(0, min(100, int(percent)))
    return start + int((bounded * span) / 100)


def _emit_windows_backup_percent(
    progress_callback: Optional[ProgressCallback],
    *,
    percent: int,
    progress_ranges: Dict[str, tuple[int, int]],
    progress_multiplier: float = STANDARD_WINDOWS_PROGRESS_MULTIPLIER,
) -> None:
    adjusted_percent = min(100, int(percent * progress_multiplier))
    _emit_progress(
        progress_callback,
        stage="windows_backup",
        step=2,
        total=6,
        message="Backing up Windows partition",
        percent=_range_percent(progress_ranges, "windows", adjusted_percent),
        detail="Backing up Windows partition",
    )


def _windows_backup_progress_multiplier(backup_type: str) -> float:
    if backup_type == "admin_compact":
        return COMPACT_WINDOWS_PROGRESS_MULTIPLIER
    return STANDARD_WINDOWS_PROGRESS_MULTIPLIER


def _run_partclone_streaming(
    argv: Sequence[str],
    *,
    output_image: Path,
    estimated_bytes: int,
    progress_callback: Optional[ProgressCallback],
    progress_ranges: Dict[str, tuple[int, int]],
    windows_progress_multiplier: float = STANDARD_WINDOWS_PROGRESS_MULTIPLIER,
) -> subprocess.CompletedProcess[str]:
    if progress_callback is None:
        result = run_command(list(argv), dry_run=False, confirmed=True)
        return subprocess.CompletedProcess(
            args=list(argv),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    process = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=0,
    )
    assert process.stdout is not None

    output_parts: list[str] = []
    pending = ""
    last_percent: Optional[int] = None
    last_emit = 0.0
    started = time.monotonic()
    expected_seconds = max(
        180.0,
        float(estimated_bytes or 0) / DEFAULT_BACKUP_THROUGHPUT_BYTES_PER_SEC,
    )

    while True:
        char = process.stdout.read(1)
        if char == "" and process.poll() is not None:
            break
        if char == "":
            continue

        output_parts.append(char)
        if char in "\r\n":
            candidate = pending
            pending = ""
        else:
            pending += char
            candidate = pending

        parsed = _extract_partclone_percent(candidate)
        now = time.monotonic()
        if parsed is None and now - last_emit >= 2.0:
            parsed = _estimated_file_percent(output_image, estimated_bytes)

        if parsed is None:
            continue
        elapsed = max(0.0, now - started)
        time_target = max(0, min(98, int((elapsed / expected_seconds) * 100)))
        target = max(parsed, time_target)
        if last_percent is None:
            display_percent = min(target, 2)
        else:
            max_step = max(1, int(((now - last_emit) / expected_seconds) * 100) + 1)
            display_percent = min(target, last_percent + max_step)

        if display_percent == last_percent and now - last_emit < 2.0:
            continue

        _emit_windows_backup_percent(
            progress_callback,
            percent=display_percent,
            progress_ranges=progress_ranges,
            progress_multiplier=windows_progress_multiplier,
        )
        last_percent = display_percent
        last_emit = now

    returncode = process.wait()
    stdout = "".join(output_parts)
    if returncode == 0:
        _emit_windows_backup_percent(
            progress_callback,
            percent=100,
            progress_ranges=progress_ranges,
            progress_multiplier=windows_progress_multiplier,
        )
    return subprocess.CompletedProcess(
        args=list(argv),
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def _ensure_windows_unmounted(layout, guard: WriteGuard) -> List[str]:
    operations: List[str] = []
    for mountpoint in _mounted_targets_for_device(layout.windows.path):
        result = run_command(
            ["umount", mountpoint],
            dry_run=False,
            confirmed=guard.confirmed,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or mountpoint).strip()
            raise RuntimeError(
                f"failed to unmount Windows partition from {mountpoint}: {detail}"
            )
        operations.append(f"unmounted Windows partition from {mountpoint}")
    return operations


def _mount_is_readonly(mount_point: Path) -> bool:
    try:
        with Path("/proc/self/mounts").open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 4:
                    continue
                if parts[1] != str(mount_point):
                    continue
                options = parts[3].split(",")
                return "ro" in options and "rw" not in options
    except OSError:
        return False
    return False


def plan_backup_run(
    guard: WriteGuard,
    *,
    backup_type: str = "standard",
    admin_backup_metadata: Optional[Dict[str, Any]] = None,
) -> BackupRunResult:
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
    ignore_ntfs_fschk = backup_type == "admin_compact"
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
            ignore_fschk=ignore_ntfs_fschk,
        ),
        "windows_used_domain": format_partclone_ntfs_domain_command(
            layout.windows.path,
            recovery_mount / WINDOWS_USED_DOMAIN_RELATIVE,
            ignore_fschk=ignore_ntfs_fschk,
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
        f"planned Windows used-block map: {commands['windows_used_domain']}",
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
    if estimate.reason:
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
                backup_type=backup_type,
                admin_backup_metadata=admin_backup_metadata,
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
            backup_type=backup_type,
            admin_backup_metadata=admin_backup_metadata,
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


def execute_backup_apply(
    guard: WriteGuard,
    *,
    progress_callback: Optional[ProgressCallback] = None,
    backup_type: str = "standard",
    admin_backup_metadata: Optional[Dict[str, Any]] = None,
) -> BackupRunResult:
    """Execute backup writes (requires --apply --confirm)."""
    plan = plan_backup_run(
        guard,
        backup_type=backup_type,
        admin_backup_metadata=admin_backup_metadata,
    )
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
    recovery_mount: Path
    try:
        runtime_log("backup start")
        _emit_progress(
            progress_callback,
            stage="prepare",
            step=1,
            total=5,
            message="Preparing backup workspace...",
            percent=0,
        )
        recovery_mount, mount_ops = _resolve_recovery_mount(layout, guard)
        operations.extend(mount_ops)
        runtime_log(f"RECOVERY_IMAGE mount: {recovery_mount}")

        if has_valid_backup(recovery_mount):
            runtime_log("backup blocked: valid backup already exists")
            return BackupRunResult(
                status="BLOCKED",
                dry_run=False,
                apply=True,
                confirmed=guard.confirmed,
                can_backup=False,
                reason="valid backup already exists",
                planned_operations=operations,
            )

        # Mark backup as "in-progress" (restore must be forbidden while this marker exists).
        mark_incomplete_backup(
            recovery_mount,
            "backup started",
            details={"mode": "apply", "note": "incomplete_backup blocks restore until manifest finalize"},
        )
        runtime_log("state/incomplete_backup created")
        finalize_log("finalize start (backup apply)")

        unmount_ops = _ensure_windows_unmounted(layout, guard)
        operations.extend(unmount_ops)
        for op in unmount_ops:
            runtime_log(op)

        gpt_out = recovery_mount / GPT_METADATA_RELATIVE
        windows_out = recovery_mount / IMAGE_RELATIVE
        windows_domain_out = recovery_mount / WINDOWS_USED_DOMAIN_RELATIVE
        progress_ranges = _build_backup_progress_ranges(plan, layout)

        _emit_progress(
            progress_callback,
            stage="prepare",
            step=1,
            total=6,
            message="Preparing backup workspace",
            percent=progress_ranges["prepare"][0],
            detail="Preparing backup workspace",
        )
        guard.mkdir(gpt_out.parent, operation="artifact-dir")
        guard.mkdir((recovery_mount / "images"), operation="artifact-dir")
        guard.mkdir((recovery_mount / "metadata"), operation="artifact-dir")
        guard.mkdir((recovery_mount / "manifests"), operation="artifact-dir")
        guard.mkdir((recovery_mount / "hashes"), operation="artifact-dir")
        _emit_progress(
            progress_callback,
            stage="prepare",
            step=1,
            total=6,
            message="Preparing backup workspace",
            percent=progress_ranges["prepare"][1],
            detail="Preparing backup workspace",
        )

        _emit_progress(
            progress_callback,
            stage="windows_backup",
            step=2,
            total=6,
            message="Backing up Windows partition",
            percent=progress_ranges["windows"][0],
            detail="Backing up Windows partition",
        )
        runtime_log("Windows backup start")
        win_cmd = format_partclone_ntfs_command(
            layout.windows.path,
            windows_out,
            ignore_fschk=backup_type == "admin_compact",
        )
        argv = shlex.split(win_cmd)
        result = _run_partclone_streaming(
            argv,
            output_image=windows_out,
            estimated_bytes=plan.estimated_used_bytes or layout.windows.size or 0,
            progress_callback=progress_callback,
            progress_ranges=progress_ranges,
            windows_progress_multiplier=_windows_backup_progress_multiplier(backup_type),
        )
        if result.returncode != 0:
            raise RuntimeError(_command_failure_detail("Windows backup", win_cmd, result))
        _emit_progress(
            progress_callback,
            stage="windows_verify",
            step=3,
            total=6,
            message="Verifying Windows backup image",
            percent=progress_ranges["windows_verify"][0],
            detail="Verifying Windows backup image",
        )
        verify_artifact_file(windows_out, label="Windows", min_size=1024 * 1024)
        _emit_progress(
            progress_callback,
            stage="windows_verify",
            step=3,
            total=6,
            message="Verifying Windows backup image",
            percent=progress_ranges["windows_verify"][1],
            detail="Verifying Windows backup image",
        )
        runtime_log("Windows backup end")
        finalize_log("windows backup complete")

        domain_cmd = format_partclone_ntfs_domain_command(
            layout.windows.path,
            windows_domain_out,
            ignore_fschk=backup_type == "admin_compact",
        )
        domain_result = run_command(
            shlex.split(domain_cmd),
            dry_run=False,
            confirmed=guard.confirmed,
        )
        if domain_result.returncode == 0 and windows_domain_out.is_file():
            operations.append(f"windows used-block map -> {windows_domain_out}")
            runtime_log("Windows used-block domain map complete")
            finalize_log("windows used-block domain map complete")
        else:
            warning = _command_failure_detail(
                "Windows used-block domain map",
                domain_cmd,
                domain_result,
            )
            operations.append(f"windows used-block map skipped: {warning}")
            runtime_log(f"Windows used-block domain map skipped: {warning}")
            finalize_log(f"windows used-block domain map skipped: {warning}")

        _emit_progress(
            progress_callback,
            stage="efi_backup",
            step=4,
            total=6,
            message="Backing up EFI partition",
            percent=progress_ranges["efi"][0],
            detail="Backing up EFI partition",
        )
        efi_out = run_efi_backup_precheck(layout.efi.path, recovery_mount)
        efi_cmd = format_partclone_fat_command(layout.efi.path, efi_out)
        runtime_log(f"EFI partclone: {efi_cmd}")
        argv = shlex.split(efi_cmd)
        result = run_command(argv, dry_run=False, confirmed=guard.confirmed)
        if result.returncode != 0:
            raise EfiBackupError(_command_failure_detail("EFI partclone.fat", efi_cmd, result))
        _emit_progress(
            progress_callback,
            stage="efi_backup",
            step=4,
            total=6,
            message="Backing up EFI partition",
            percent=progress_ranges["efi"][1],
            detail="Backing up EFI partition",
        )
        _emit_progress(
            progress_callback,
            stage="efi_verify",
            step=4,
            total=6,
            message="Verifying EFI backup image",
            percent=progress_ranges["efi_verify"][0],
            detail="Verifying EFI backup image",
        )
        verify_efi_artifact(recovery_mount)
        _emit_progress(
            progress_callback,
            stage="efi_verify",
            step=4,
            total=6,
            message="Verifying EFI backup image",
            percent=progress_ranges["efi_verify"][1],
            detail="Verifying EFI backup image",
        )
        runtime_log("EFI backup end")
        finalize_log("efi backup complete")

        _emit_progress(
            progress_callback,
            stage="gpt_backup",
            step=5,
            total=6,
            message="Backing up GPT metadata",
            percent=progress_ranges["gpt"][0],
            detail="Backing up GPT metadata",
        )
        runtime_log("GPT backup start")
        gpt_cmd = format_gpt_backup_command(layout.disk_path, gpt_out)
        argv = shlex.split(gpt_cmd)
        result = run_command(argv, dry_run=False, confirmed=guard.confirmed)
        if result.returncode != 0:
            raise RuntimeError(_command_failure_detail("GPT backup", gpt_cmd, result))
        _emit_progress(
            progress_callback,
            stage="gpt_backup",
            step=5,
            total=6,
            message="Backing up GPT metadata",
            percent=progress_ranges["gpt"][1],
            detail="Backing up GPT metadata",
        )
        _emit_progress(
            progress_callback,
            stage="gpt_verify",
            step=5,
            total=6,
            message="Verifying GPT metadata backup",
            percent=progress_ranges["gpt_verify"][0],
            detail="Verifying GPT metadata backup",
        )
        verify_artifact_file(gpt_out, label="GPT", min_size=512)
        _emit_progress(
            progress_callback,
            stage="gpt_verify",
            step=5,
            total=6,
            message="Verifying GPT metadata backup",
            percent=progress_ranges["gpt_verify"][1],
            detail="Verifying GPT metadata backup",
        )
        runtime_log("GPT backup end")
        finalize_log("gpt backup complete")

        _emit_progress(
            progress_callback,
            stage="metadata",
            step=6,
            total=6,
            message="Collecting backup metadata",
            percent=progress_ranges["metadata"][0],
            detail="Collecting backup metadata",
        )
        runtime_log("hash generation: start (finalize transaction)")
        disk = build_disk_metadata(layout)
        ctx = ManifestContext(
            recovery_root=recovery_mount,
            disk=disk,
            backup_type=backup_type,
            restore_baseline_bytes=(
                int(admin_backup_metadata["restore_baseline_bytes"])
                if admin_backup_metadata
                and admin_backup_metadata.get("restore_baseline_bytes") is not None
                else None
            ),
            source_windows_partition_size_bytes=(
                int(admin_backup_metadata["source_windows_partition_size_bytes"])
                if admin_backup_metadata
                and admin_backup_metadata.get("source_windows_partition_size_bytes") is not None
                else None
            ),
            admin_backup=dict(admin_backup_metadata or {}),
        )
        _emit_progress(
            progress_callback,
            stage="metadata",
            step=6,
            total=6,
            message="Collecting backup metadata",
            percent=progress_ranges["metadata"][1],
            detail="Collecting backup metadata",
        )
        _emit_progress(
            progress_callback,
            stage="hash_generation",
            step=6,
            total=6,
            message="Generating backup hashes",
            percent=progress_ranges["hash_generation"][0],
            detail="Generating backup hashes",
        )

        def _finalize_progress(event: dict[str, Any]) -> None:
            total_bytes = int(event.get("bytes_total") or 0)
            done_bytes = int(event.get("bytes_done") or 0)
            if total_bytes <= 0:
                return
            hash_percent = max(0, min(100, int((done_bytes / total_bytes) * 100)))
            phase = str(event.get("phase") or "")
            if phase == "hash_validation":
                percent = _range_percent(progress_ranges, "hash_validation", hash_percent)
                detail = "Verifying backup hashes"
            else:
                percent = _range_percent(progress_ranges, "hash_generation", hash_percent)
                detail = "Generating backup hashes"
            _emit_progress(
                progress_callback,
                stage=phase or "hash_generation",
                step=6,
                total=6,
                message=detail,
                percent=percent,
                detail=detail,
            )

        finalize_backup_manifest(ctx, progress_callback=_finalize_progress)
        _emit_progress(
            progress_callback,
            stage="final_closeout",
            step=6,
            total=6,
            message="Finalizing backup metadata",
            percent=progress_ranges["final_closeout"][1],
            detail="Finalizing backup metadata",
        )
        runtime_log("manifest generation: complete")
        runtime_log("finalize validation: complete")
        runtime_log("incomplete_backup cleared")
        runtime_log("backup success")
        operations.append("finalized manifests/recovery-manifest.json")
        _emit_progress(
            progress_callback,
            stage="complete",
            step=6,
            total=6,
            message="Recovery backup completed.",
            percent=100,
            final=True,
        )

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
        reason = str(exc)
        runtime_log(f"backup failure: {reason}")
        mount = recovery_mount if "recovery_mount" in locals() else Path(DEFAULT_EXPECTED_MOUNT)
        mark_incomplete_backup(mount, reason)
        runtime_log("incomplete_backup retained (fail-closed)")
        return BackupRunResult(
            status="FAILED",
            dry_run=False,
            apply=True,
            confirmed=guard.confirmed,
            can_backup=False,
            reason=str(exc),
            planned_operations=operations,
        )


def run_backup(
    *,
    apply: bool,
    confirmed: bool,
    progress_callback: Optional[ProgressCallback] = None,
    backup_type: str = "standard",
    admin_backup_metadata: Optional[Dict[str, Any]] = None,
) -> BackupRunResult:
    guard = WriteGuard(apply=apply, confirmed=confirmed)
    if guard.dry_run:
        return plan_backup_run(
            guard,
            backup_type=backup_type,
            admin_backup_metadata=admin_backup_metadata,
        )
    return execute_backup_apply(
        guard,
        progress_callback=progress_callback,
        backup_type=backup_type,
        admin_backup_metadata=admin_backup_metadata,
    )


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

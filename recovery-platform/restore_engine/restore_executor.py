"""Destructive restore execution (requires prior safety authorization)."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from backup_engine.backup_planner import discover_layout
from backup_engine.backup_state import DEFAULT_IMAGE_FILES
from backup_engine.hash import sha256_file
from backup_engine.manifest import DiskMetadata, load_recovery_manifest
from backup_engine.run_backup import build_disk_metadata
from boot_manager.bootorder_planner import plan_bootorder_recovery
from common.command import run_command, run_readonly
from common.errors import RestoreSafetyError
from common.logger import get_logger
from partition_manager.models import LABEL_RECOVERY_IMAGE, LABEL_RECOVERY_LINUX
from recovery_runtime.mounts import mount_point_for_label
from recovery_runtime.state import RuntimeState
from restore_engine.partclone_restore import (
    BOOTMGFW_EFI_PATH,
    format_gpt_snapshot_command,
    format_partclone_fat_restore_command,
    format_partclone_ntfs_restore_command,
)
from restore_engine.restore_planner import _find_efi_mount, _simulated_firmware_analysis
from restore_engine.restore_safety import RestoreSafetyResult
from restore_engine.restore_state import (
    logs_dir,
    mark_restore_failed,
    mark_restore_started,
    mark_restore_success,
)
from rollback.failure_counter import record_restore_failure_state
from validation.image_validation import (
    validate_restore,
    validate_restore_compatible,
    validate_restore_compatible_quick,
    validate_restore_quick,
)

logger = get_logger(__name__)

ProgressCallback = Callable[[Dict[str, Any]], None]
PARTCLONE_PROGRESS_RE = re.compile(r"(?P<percent>\d+(?:\.\d+)?)\s*%")

from restore_engine.restore_paths import EFI_SNAPSHOT_DIR, GPT_SNAPSHOT_FILE, PRE_RESTORE_DIR

RECOVERY_BOOT_RELATIVE = Path("EFI/RecoveryBoot")
BOOTMGFW_RELATIVE = Path(BOOTMGFW_EFI_PATH)

EFI_SURVIVABILITY_FILES = (
    BOOTMGFW_RELATIVE,
    Path("EFI/RecoveryBoot/shimx64.efi"),
    Path("EFI/RecoveryBoot/grubx64.efi"),
    Path("EFI/RecoveryBoot/grub.cfg"),
)


@dataclass(frozen=True)
class RestoreExecutionContext:
    """Authorized restore execution inputs."""

    safety: RestoreSafetyResult
    recovery_root: Path
    layout: Any
    disk: DiskMetadata
    confirmed: bool = True
    runtime_state: Optional[RuntimeState] = None
    progress_callback: Optional[ProgressCallback] = None
    prevalidated: bool = False


@dataclass
class RestoreExecutionResult:
    """Outcome of a restore execution attempt."""

    status: str
    success: bool
    reason: Optional[str] = None
    current_stage: str = "idle"
    rollback_required: bool = False
    operations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RestoreLogWriter:
    """Append-only logs under RECOVERY_IMAGE/logs (failures are non-fatal)."""

    def __init__(self, recovery_root: Path) -> None:
        self._dir = logs_dir(recovery_root)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def append(self, filename: str, message: str) -> None:
        try:
            path = self._dir / filename
            stamp = datetime.now(timezone.utc).isoformat()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} | {message}\n")
        except OSError as exc:
            logger.debug("log write skipped for %s: %s", filename, exc)


class RestoreExecutor:
    """
    Execute destructive restore steps only after safety authorization.

    Windows boot survivability is prioritized over RecoveryBoot restoration.
    """

    def __init__(self, ctx: RestoreExecutionContext) -> None:
        if not ctx.safety.allowed:
            raise RestoreSafetyError(
                ctx.safety.reason or "restore execution not authorized"
            )
        if not ctx.confirmed:
            raise RestoreSafetyError("restore execution requires --confirm")
        self._ctx = ctx
        self._layout = ctx.layout
        self._disk = ctx.disk
        self._recovery_root = ctx.recovery_root
        self._confirmed = ctx.confirmed
        self._operations: List[str] = []
        self._logs = RestoreLogWriter(ctx.recovery_root)
        self._runtime = ctx.runtime_state
        self._progress_callback = ctx.progress_callback
        self._efi_mount: Optional[Path] = None
        self._forbidden_targets = self._protected_partition_paths()

    @property
    def authorization(self) -> RestoreSafetyResult:
        return self._ctx.safety

    def _protected_partition_paths(self) -> set[str]:
        paths = {
            self._layout.recovery_image.path,
        }
        for vol in getattr(self._layout, "volumes", []) or []:
            label = getattr(vol, "label", None)
            if label in {LABEL_RECOVERY_IMAGE, LABEL_RECOVERY_LINUX}:
                paths.add(vol.path)
        return paths

    def _assert_authorized(self, operation: str) -> None:
        if not self._ctx.safety.allowed:
            raise RestoreSafetyError(
                f"destructive operation blocked (not authorized): {operation}"
            )

    def _assert_target_allowed(self, device_path: str, operation: str) -> None:
        if device_path in self._forbidden_targets:
            raise RestoreSafetyError(
                f"refusing {operation} on protected partition {device_path}"
            )

    def _log(self, filename: str, message: str) -> None:
        self._logs.append(filename, message)
        logger.info("[%s] %s", filename, message)

    def _emit_progress(
        self,
        *,
        stage: str,
        percent: int,
        message: str,
        final: bool = False,
    ) -> None:
        if self._progress_callback is None:
            return
        payload: Dict[str, Any] = {
            "stage": stage,
            "percent": max(0, min(100, int(percent))),
            "message": message,
            "detail": message,
        }
        if final:
            payload["final"] = True
        self._progress_callback(payload)

    @contextmanager
    def _progress_heartbeat(
        self,
        *,
        stage: str,
        start_percent: int,
        end_percent: int,
        message: str,
        interval: float = 2.0,
    ) -> Iterator[None]:
        if self._progress_callback is None:
            yield
            return

        stop = threading.Event()

        def _run() -> None:
            percent = start_percent
            while not stop.is_set():
                self._emit_progress(stage=stage, percent=percent, message=message)
                percent = min(end_percent, percent + 1)
                stop.wait(interval)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        try:
            yield
        finally:
            stop.set()
            worker.join(timeout=interval)

    @staticmethod
    def _extract_partclone_percent(text: str) -> Optional[int]:
        matches = list(PARTCLONE_PROGRESS_RE.finditer(text))
        if not matches:
            return None
        try:
            value = float(matches[-1].group("percent"))
        except ValueError:
            return None
        return max(0, min(100, int(round(value))))

    def _run_confirmed_streaming_partclone(
        self,
        command: str,
        *,
        operation: str,
        progress_start: int,
        progress_end: int,
        message: str,
    ) -> None:
        self._assert_authorized(operation)
        argv = shlex.split(command)
        target = argv[-1] if argv else ""
        self._assert_target_allowed(target, operation)

        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
        )
        assert process.stdout is not None
        output_parts: List[str] = []
        pending = ""
        last_percent: Optional[int] = None
        last_emit = 0.0

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

            parsed = self._extract_partclone_percent(candidate)
            now = time.monotonic()
            if parsed is None:
                continue
            if parsed == last_percent and now - last_emit < 1.0:
                continue
            span = max(1, progress_end - progress_start)
            display = progress_start + int((parsed * span) / 100)
            self._emit_progress(
                stage=operation,
                percent=display,
                message=message,
            )
            last_percent = parsed
            last_emit = now

        returncode = process.wait()
        output = "".join(output_parts).strip()
        self._operations.append(command)
        if returncode != 0:
            raise RuntimeError(f"{operation} failed (rc={returncode}): {output}")
        self._emit_progress(stage=operation, percent=progress_end, message=message)

    def _fail(self, stage: str, reason: str) -> RestoreExecutionResult:
        self._log("error.log", f"{stage}: {reason}")
        self._log("rollback.log", f"rollback_required=true reason={reason}")
        mark_restore_failed(self._recovery_root, reason, stage=stage)
        record_restore_failure_state(self._recovery_root, reason, stage=stage)
        if self._runtime is not None:
            self._runtime.restore_enabled = False
            self._runtime.destructive_allowed = False
            self._runtime.last_message = reason
        return RestoreExecutionResult(
            status="FAILED",
            success=False,
            reason=reason,
            current_stage=stage,
            rollback_required=True,
            operations=list(self._operations),
        )

    def _run_confirmed(self, command: str, *, operation: str) -> None:
        self._assert_authorized(operation)
        argv = shlex.split(command)
        if len(argv) >= 2 and argv[0] == "partclone.ntfs":
            target = argv[-1]
            self._assert_target_allowed(target, operation)
        if len(argv) >= 2 and argv[0] == "partclone.fat":
            target = argv[-1]
            self._assert_target_allowed(target, operation)
        result = run_command(argv, dry_run=False, confirmed=self._confirmed)
        self._operations.append(command)
        if result.returncode != 0:
            raise RuntimeError(
                f"{operation} failed (rc={result.returncode}): {result.stderr.strip()}"
            )

    @staticmethod
    def _command_detail(result: Any, label: str) -> str:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        return f"{label} failed (rc={getattr(result, 'returncode', 'unknown')}): {detail}"

    def execute(self) -> RestoreExecutionResult:
        """Run the full destructive restore pipeline."""
        self._assert_authorized("restore_pipeline")

        try:
            self._emit_progress(stage="pre_restore_validation", percent=20, message="Checking restore readiness")
            compatible_restore = bool(getattr(self._ctx.safety, "compatible_restore", False))
            if compatible_restore:
                validation = (
                    validate_restore_compatible_quick(self._recovery_root, self._disk)
                    if self._ctx.prevalidated
                    else validate_restore_compatible(self._recovery_root, self._disk)
                )
            else:
                validation = (
                    validate_restore_quick(self._recovery_root, self._disk)
                    if self._ctx.prevalidated
                    else validate_restore(self._recovery_root, self._disk)
                )
            if not validation.allowed:
                return self._fail(
                    "pre_restore_validation",
                    validation.reason or "validate_restore failed",
                )
            self._emit_progress(stage="pre_restore_validation", percent=22, message="Restore readiness verified")
            self._stage_backup_efi()
            self._emit_progress(stage="efi_backup", percent=25, message="Backing up EFI safety files")
            self._stage_backup_gpt()
            self._emit_progress(stage="gpt_backup", percent=28, message="Backing up GPT metadata")
            mark_restore_started(self._recovery_root)
            self._operations.append("restore_in_progress=true")
            self._stage_ensure_windows_unmounted()
            self._emit_progress(stage="windows_unmount", percent=30, message="Preparing Windows partition")
            self._stage_partclone_windows()
            self._emit_progress(stage="windows_restore", percent=90, message="Windows partition restored")
            with self._progress_heartbeat(
                stage="restore_finalize",
                start_percent=91,
                end_percent=98,
                message="Finalizing system restore",
                interval=4.0,
            ):
                self._stage_restore_recovery_boot_efi()
                self._emit_progress(stage="efi_restore", percent=92, message="Restoring RecoveryBoot EFI files")
                self._stage_verify_windows_boot_manager()
                self._emit_progress(stage="boot_verify", percent=95, message="Verifying Windows Boot Manager")
                self._stage_repair_bootorder()
                self._emit_progress(stage="bootorder", percent=98, message="Checking RecoveryBoot order")
                mark_restore_success(self._recovery_root)
                self._stage_log_integrity()
            if self._runtime is not None:
                self._runtime.restore_enabled = True
                self._runtime.last_message = "restore completed"
            self._log("restore.log", "restore completed successfully")
            self._emit_progress(
                stage="complete",
                percent=100,
                message="System restore completed.",
                final=True,
            )
            return RestoreExecutionResult(
                status="COMPLETED",
                success=True,
                reason=None,
                current_stage="restore_complete",
                rollback_required=False,
                operations=list(self._operations),
            )
        except Exception as exc:
            logger.exception("restore execution failed at %s", exc)
            return self._fail("restore_execution", str(exc))
        finally:
            self._try_unmount_efi()

    def _stage_backup_efi(self) -> None:
        self._assert_authorized("efi_backup")
        self._log("restore.log", "stage: backup EFI survivability assets")
        snapshot_root = self._recovery_root / EFI_SNAPSHOT_DIR
        if snapshot_root.exists():
            shutil.rmtree(snapshot_root)
        snapshot_root.mkdir(parents=True, exist_ok=True)

        mount = self._mount_efi(read_only=True)
        efi_tree = mount / "EFI"
        if not efi_tree.is_dir():
            raise RuntimeError("EFI directory tree not found on ESP")

        for relative in EFI_SURVIVABILITY_FILES:
            source = mount / relative
            dest = snapshot_root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.is_file():
                shutil.copy2(source, dest)
            elif source.is_dir():
                shutil.copytree(source, dest, dirs_exist_ok=True)

        bootmgfw = mount / BOOTMGFW_RELATIVE
        if not bootmgfw.is_file():
            raise RuntimeError("bootmgfw.efi missing before restore; aborting")
        self._operations.append(f"efi backup -> {snapshot_root}")

    def _stage_backup_gpt(self) -> None:
        self._assert_authorized("gpt_backup")
        self._log("restore.log", "stage: backup live GPT metadata")
        output = self._recovery_root / GPT_SNAPSHOT_FILE
        output.parent.mkdir(parents=True, exist_ok=True)
        disk = self._layout.disk_path
        self._assert_target_allowed(disk, "gpt_backup")
        cmd = format_gpt_snapshot_command(output, disk)
        self._run_confirmed(cmd, operation="gpt_backup")
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("GPT backup file missing or empty")

    def _stage_ensure_windows_unmounted(self) -> None:
        self._assert_authorized("windows_unmount")
        windows = self._layout.windows.path
        self._assert_target_allowed(windows, "windows_unmount")
        mounted = run_readonly(["findmnt", "-n", "-o", "TARGET", windows])
        if mounted.returncode == 0 and mounted.stdout.strip():
            mountpoint = mounted.stdout.strip()
            self._log("restore.log", f"unmounting Windows partition at {mountpoint}")
            result = run_command(
                ["umount", mountpoint],
                dry_run=False,
                confirmed=self._confirmed,
            )
            if result.returncode != 0:
                raise RuntimeError(f"failed to unmount Windows partition: {result.stderr}")
            self._operations.append(f"umount {mountpoint}")

    def _stage_partclone_windows(self) -> None:
        self._assert_authorized("windows_partclone_restore")
        image = self._recovery_root / DEFAULT_IMAGE_FILES["windows"]
        if not image.is_file():
            raise RuntimeError(f"Windows image missing: {image}")
        windows = self._layout.windows.path
        cmd = format_partclone_ntfs_restore_command(image, windows)
        self._log("restore.log", f"stage: {cmd}")
        self._run_confirmed_streaming_partclone(
            cmd,
            operation="windows_partclone_restore",
            progress_start=30,
            progress_end=90,
            message="Restoring Windows partition",
        )

    def _stage_restore_recovery_boot_efi(self) -> None:
        self._assert_authorized("efi_recovery_boot_restore")
        self._log("restore.log", "stage: restore RecoveryBoot EFI only (bootmgfw.efi protected)")
        mount = self._mount_efi(read_only=False)
        bootmgfw = mount / BOOTMGFW_RELATIVE
        if not bootmgfw.is_file():
            raise RuntimeError(
                "bootmgfw.efi missing during EFI restore; Windows boot survivability at risk"
            )

        snapshot = self._recovery_root / EFI_SNAPSHOT_DIR / RECOVERY_BOOT_RELATIVE
        target = mount / RECOVERY_BOOT_RELATIVE
        if snapshot.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(snapshot, target, dirs_exist_ok=True)
            self._operations.append(f"restored RecoveryBoot from snapshot {snapshot}")
            return

        image = self._recovery_root / DEFAULT_IMAGE_FILES["efi"]
        if not image.is_file():
            raise RuntimeError(f"EFI image missing: {image}")
        # partclone to partition would overwrite bootmgfw; use image only if snapshot absent
        # and verify bootmgfw still present after (not executed to whole ESP in this path).
        raise RuntimeError(
            "RecoveryBoot EFI snapshot missing; refusing full ESP partclone restore"
        )

    def _stage_verify_windows_boot_manager(self) -> None:
        self._assert_authorized("windows_boot_manager_verify")
        mount = self._mount_efi(read_only=True)
        bootmgfw = mount / BOOTMGFW_RELATIVE
        if not bootmgfw.is_file():
            raise RuntimeError("Windows Boot Manager missing (bootmgfw.efi not found)")
        self._log("integrity.log", f"bootmgfw.efi present sha256={sha256_file(bootmgfw)}")
        self._operations.append("verified bootmgfw.efi")

    def _stage_repair_bootorder(self) -> None:
        self._assert_authorized("bootorder_repair")
        firmware = _simulated_firmware_analysis()
        if sys.platform == "win32":
            from boot_manager.firmware_reader import read_firmware_boot

            firmware = read_firmware_boot(dry_run=False)
        plan = plan_bootorder_recovery(firmware, dry_run=False)
        for cmd in plan.commands:
            self._log("rollback.log", f"planned bootorder command: {cmd}")
            if sys.platform == "win32":
                self._run_confirmed(cmd, operation="bootorder_repair")
            else:
                self._operations.append(f"planned (linux): {cmd}")
        if plan.action_required and sys.platform != "win32":
            self._log(
                "restore.log",
                "BootOrder repair deferred on Linux; bootmgfw.efi survivability preserved",
            )
        self._operations.append("bootorder repair stage complete")

    def _stage_log_integrity(self) -> None:
        manifest = load_recovery_manifest(self._recovery_root)
        declared = manifest.get("sha256_hashes") or {}
        for relative in declared:
            path = self._recovery_root / relative
            if path.is_file():
                digest = sha256_file(path)
                self._log("integrity.log", f"{relative} sha256={digest}")

    def _mount_efi(self, *, read_only: bool) -> Path:
        efi_path = self._layout.efi.path
        self._assert_target_allowed(efi_path, "efi_mount")
        existing = _find_efi_mount(efi_path)
        if existing is not None:
            if read_only:
                self._efi_mount = existing
                return existing
            result = run_command(
                ["umount", str(existing)],
                dry_run=False,
                confirmed=self._confirmed,
            )
            if result.returncode != 0:
                raise RuntimeError(f"EFI unmount failed: {result.stderr.strip()}")
            self._operations.append(f"umount {existing}")
            mountpoint = existing
        else:
            mountpoint = mount_point_for_label("ESP_RESTORE")

        mountpoint.mkdir(parents=True, exist_ok=True)
        opts = "ro" if read_only else "rw"
        result = run_command(
            ["mount", "-o", opts, efi_path, str(mountpoint)],
            dry_run=False,
            confirmed=self._confirmed,
        )
        if result.returncode != 0:
            raise RuntimeError(f"EFI mount failed: {result.stderr.strip()}")
        self._efi_mount = mountpoint
        self._operations.append(f"mount {efi_path} -> {mountpoint} ({opts})")
        return mountpoint

    def _try_unmount_efi(self) -> None:
        if self._efi_mount is None:
            return
        if _find_efi_mount(self._layout.efi.path) == self._efi_mount:
            result = run_command(
                ["umount", str(self._efi_mount)],
                dry_run=False,
                confirmed=self._confirmed,
            )
            if result.returncode == 0:
                self._operations.append(f"umount {self._efi_mount}")


def build_execution_context(
    safety: RestoreSafetyResult,
    *,
    confirmed: bool,
    recovery_root: Optional[Path] = None,
    runtime_state: Optional[RuntimeState] = None,
    progress_callback: Optional[ProgressCallback] = None,
    prevalidated: bool = False,
) -> RestoreExecutionContext:
    """Resolve layout and disk metadata after successful authorization."""
    if not safety.allowed:
        raise RestoreSafetyError(safety.reason or "restore not authorized")

    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        raise RestoreSafetyError(topology_reason or "layout discovery failed")

    mount = layout.recovery_image.mountpoint
    if recovery_root is None:
        if not mount:
            raise RestoreSafetyError("RECOVERY_IMAGE must be mounted")
        recovery_root = Path(mount)

    disk = build_disk_metadata(layout)
    return RestoreExecutionContext(
        safety=safety,
        recovery_root=recovery_root,
        layout=layout,
        disk=disk,
        confirmed=confirmed,
        runtime_state=runtime_state,
        progress_callback=progress_callback,
        prevalidated=prevalidated,
    )

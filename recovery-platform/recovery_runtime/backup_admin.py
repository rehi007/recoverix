"""Privileged runtime helper for backup and restore TUI actions.

This module is invoked via a root-owned wrapper from the unprivileged
runtime menu. It performs destructive backup/restore operations and privileged
restore validation with the capabilities those operations require.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Sequence

from backup_engine.run_backup import plan_backup_run, run_backup
from backup_engine.write_guard import WriteGuard
from common.command import run_command, run_readonly
from common.errors import ConfirmationRequiredError
from common.logger import setup_logging
from recovery_runtime.admin_bridge import RUNTIME_PROGRESS_PREFIX
from partition_manager.models import LABEL_RECOVERY_IMAGE
from partition_manager.windows_extend import (
    plan_windows_partition_extend,
    run_windows_partition_extend,
)
from recovery_runtime.discover import discover_volumes, find_by_label, require_linux
from recovery_runtime.mounts import mount_point_for_label
from restore_engine.restore_executor import RestoreExecutor, build_execution_context
from restore_engine.restore_planner import build_restore_plan
from restore_engine.restore_safety import authorize_restore_execution
from restore_engine.restore_state import (
    RecoveryState,
    load_recovery_state,
    logs_dir,
    save_recovery_state,
)

BACKUP_WORKDIR = Path("/tmp/recoverix-backup-work")
SYSTEMD_RUN_CHILD_ENV = "RECOVERIX_BACKUP_ADMIN_CHILD"
SYSTEMD_RUN_PATH = "/usr/bin/systemd-run"
SYSTEMD_RUN_UNIT_PREFIX = "recoverix-backup-admin"
RECOVERIX_PYTHONPATH = "/usr/local/lib/recoverix"
RECOVERIX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
PYTHON3_PATH = "/usr/bin/python3"
WINDOWS_BOOT_MANAGER_LABEL = "Windows Boot Manager"
ADMIN_BACKUP_DISABLED_REASON = (
    "Administrator compact backup is no longer supported. "
    "Create a standard backup instead."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("recoverix-backup-admin must run as root")


def _json_print(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _emit_progress(payload: Dict[str, Any]) -> None:
    print(f"{RUNTIME_PROGRESS_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


@contextmanager
def _progress_heartbeat(
    progress_callback: Optional[Callable[[Dict[str, Any]], None]],
    *,
    stage: str,
    start_percent: int,
    end_percent: int,
    message: str,
    interval: float = 2.0,
) -> Iterator[None]:
    if progress_callback is None:
        yield
        return

    stop = threading.Event()

    def _run() -> None:
        percent = start_percent
        while not stop.is_set():
            progress_callback(
                {
                    "stage": stage,
                    "percent": percent,
                    "message": message,
                    "detail": message,
                }
            )
            percent = min(end_percent, percent + 1)
            stop.wait(interval)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=interval)


def _systemd_run_command(argv: Sequence[str]) -> list[str]:
    unit_name = f"{SYSTEMD_RUN_UNIT_PREFIX}-{os.getpid()}"
    return [
        SYSTEMD_RUN_PATH,
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--service-type=exec",
        f"--unit={unit_name}",
        "--property=StandardInput=null",
        f"--setenv={SYSTEMD_RUN_CHILD_ENV}=1",
        f"--setenv=PYTHONPATH={RECOVERIX_PYTHONPATH}",
        f"--setenv=PATH={RECOVERIX_PATH}",
        PYTHON3_PATH,
        "-m",
        "recovery_runtime.backup_admin",
        *argv,
    ]


def _run_via_transient_root_service(argv: Sequence[str]) -> int:
    process = subprocess.Popen(
        _systemd_run_command(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    return process.wait()


def _write_audit_log(root: Path, message: str) -> None:
    try:
        log_dir = logs_dir(root)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "audit.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{_utc_now()} | {message}\n")
    except OSError:
        pass


def _mounted_path_for_device(device_path: str) -> Optional[Path]:
    try:
        with Path("/proc/self/mounts").open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device_path:
                    return Path(parts[1])
    except OSError:
        return None
    return None


def _unmount_recovery_image(device_path: str, mount_root: Path) -> bool:
    candidates = [
        ["umount", str(mount_root)],
        ["umount", device_path],
        ["umount", "-l", str(mount_root)],
        ["umount", "-l", device_path],
    ]
    for argv in candidates:
        result = run_command(argv, dry_run=False, confirmed=True)
        if result.returncode != 0:
            continue
        if _mounted_path_for_device(device_path) is None:
            return True
    return _mounted_path_for_device(device_path) is None


def _mkfs_command(device: str, fstype: str, label: str) -> list[str]:
    normalized = (fstype or "ext4").strip().lower()
    if normalized in {"ext2", "ext3", "ext4"}:
        return [f"mkfs.{normalized}", "-F", "-L", label, device]
    if normalized == "xfs":
        return ["mkfs.xfs", "-f", "-L", label, device]
    if normalized in {"vfat", "fat", "fat32"}:
        return ["mkfs.vfat", "-F", "32", "-n", label, device]
    return ["mkfs.ext4", "-F", "-L", label, device]


def _require_mounted_recovery_image() -> tuple[Any, Path]:
    volume = find_by_label(discover_volumes(), LABEL_RECOVERY_IMAGE)
    if volume is None:
        raise RuntimeError("RECOVERY_IMAGE is unavailable")
    if not volume.mountpoint:
        raise RuntimeError("RECOVERY_IMAGE is not mounted")
    return volume, Path(volume.mountpoint)


def _remount_recovery_image(volume: Any, root: Path, mode: str) -> None:
    result = run_command(
        ["mount", "-o", f"remount,{mode}", volume.path, str(root)],
        dry_run=False,
        confirmed=True,
    )
    if result.returncode != 0:
        mode_name = "read-only" if mode == "ro" else "read-write"
        raise RuntimeError(
            f"failed to remount RECOVERY_IMAGE {mode_name}: {result.stderr.strip() or volume.path}"
        )


def _reset_recovery_image_in_place(root: Path) -> None:
    for entry in root.iterdir():
        if entry.name == "lost+found":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    for relative in ("images", "metadata", "manifests", "hashes", "logs", "state"):
        (root / relative).mkdir(parents=True, exist_ok=True)

    save_recovery_state(root, RecoveryState())


def reset_recovery_image_for_backup() -> Path:
    require_linux()
    _require_root()

    volume = find_by_label(discover_volumes(), LABEL_RECOVERY_IMAGE)
    if volume is None:
        raise RuntimeError("RECOVERY_IMAGE is unavailable")
    if volume.label != LABEL_RECOVERY_IMAGE:
        raise RuntimeError(f"refusing to format unexpected label: {volume.label!r}")

    root = Path(volume.mountpoint) if volume.mountpoint else mount_point_for_label(LABEL_RECOVERY_IMAGE)

    live_discovered = find_by_label(discover_volumes(), LABEL_RECOVERY_IMAGE)
    if live_discovered is not None and live_discovered.path != volume.path:
        raise RuntimeError(
            f"refusing to format {volume.path}: RECOVERY_IMAGE currently resolves to {live_discovered.path}"
        )
    if live_discovered is None:
        blkid = run_readonly(["blkid", "-o", "value", "-s", "LABEL", volume.path])
        live_label = blkid.stdout.strip().upper() if blkid.returncode == 0 else ""
        if live_label != LABEL_RECOVERY_IMAGE:
            raise RuntimeError(
                f"refusing to format {volume.path}: live label is {live_label or 'unknown'}"
            )

    _write_audit_log(root, "backup format requested")
    if not _unmount_recovery_image(volume.path, root):
        result = run_command(
            ["mount", "-o", "remount,rw", volume.path, str(root)],
            dry_run=False,
            confirmed=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"failed to remount RECOVERY_IMAGE read-write: {result.stderr.strip() or volume.path}"
            )
        _reset_recovery_image_in_place(root)
        _write_audit_log(root, "backup reset completed (in-place fallback)")
        return root

    mkfs_cmd = _mkfs_command(volume.path, volume.fstype or "ext4", LABEL_RECOVERY_IMAGE)
    result = run_command(mkfs_cmd, dry_run=False, confirmed=True)
    if result.returncode != 0:
        raise RuntimeError(f"RECOVERY_IMAGE format failed: {' '.join(mkfs_cmd)}")

    root.mkdir(parents=True, exist_ok=True)
    result = run_command(
        ["mount", "-o", "rw", volume.path, str(root)],
        dry_run=False,
        confirmed=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to remount formatted RECOVERY_IMAGE at {root}")

    for relative in ("images", "metadata", "manifests", "hashes", "logs", "state"):
        (root / relative).mkdir(parents=True, exist_ok=True)

    save_recovery_state(root, RecoveryState())
    _write_audit_log(root, "backup format completed")
    return root


def run_backup_apply() -> Dict[str, Any]:
    require_linux()
    _require_root()
    BACKUP_WORKDIR.mkdir(parents=True, exist_ok=True)
    os.chdir(BACKUP_WORKDIR)
    result = run_backup(
        apply=True,
        confirmed=True,
        progress_callback=_emit_progress,
    )
    return result.to_dict()


def run_backup_plan() -> Dict[str, Any]:
    require_linux()
    _require_root()
    return plan_backup_run(WriteGuard(apply=False, confirmed=True)).to_dict()


def run_admin_backup_plan() -> Dict[str, Any]:
    require_linux()
    _require_root()
    return {
        "status": "DISABLED",
        "can_create": False,
        "reason": ADMIN_BACKUP_DISABLED_REASON,
    }


def run_admin_backup_apply() -> Dict[str, Any]:
    require_linux()
    _require_root()
    return {
        "status": "DISABLED",
        "reason": ADMIN_BACKUP_DISABLED_REASON,
    }


def run_windows_extend_plan() -> Dict[str, Any]:
    require_linux()
    _require_root()
    _volume, root = _require_mounted_recovery_image()
    return plan_windows_partition_extend(recovery_root=root).to_dict()


def run_windows_extend_apply() -> Dict[str, Any]:
    require_linux()
    _require_root()
    volume, root = _require_mounted_recovery_image()
    _remount_recovery_image(volume, root, "rw")
    try:
        return run_windows_partition_extend(backup_root=root)
    finally:
        try:
            _remount_recovery_image(volume, root, "ro")
        except RuntimeError:
            pass


def run_restore_check() -> Dict[str, Any]:
    require_linux()
    _require_root()
    plan = build_restore_plan(live=True, fast_validation=True)
    return {
        "status": "COMPLETED",
        "validation": dict(plan.validation),
        "restore_plan": plan.to_dict(),
    }


def run_restore_apply(
    phrase: str,
    *,
    compatible_restore: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    require_linux()
    _require_root()
    volume, root = _require_mounted_recovery_image()
    _remount_recovery_image(volume, root, "rw")
    try:
        with _progress_heartbeat(
            progress_callback,
            stage="restore_safety",
            start_percent=1,
            end_percent=18,
            message="Validating restore image",
        ):
            safety = authorize_restore_execution(
                apply=True,
                confirmed=True,
                confirmation_phrase=phrase,
                compatible_restore=compatible_restore,
            )
        if progress_callback is not None:
            progress_callback(
                {
                    "stage": "restore_safety",
                    "percent": 19,
                    "message": "Restore image validation completed",
                    "detail": "Restore image validation completed",
                }
            )
        exec_ctx = build_execution_context(
            safety,
            confirmed=True,
            progress_callback=progress_callback,
            prevalidated=True,
        )
        result = RestoreExecutor(exec_ctx).execute()
        result_status = getattr(result, "status", None)
        status = result_status if result.success and isinstance(result_status, str) else "COMPLETED"
        if not result.success:
            status = "FAILED"
        return {
            "status": status,
            "reason": result.reason,
            "result": result.to_dict(),
            "safety": safety.to_dict(),
        }
    finally:
        try:
            _remount_recovery_image(volume, root, "ro")
        except RuntimeError:
            pass


def delete_backup_data() -> Dict[str, Any]:
    require_linux()
    _require_root()

    volume, root = _require_mounted_recovery_image()
    _write_audit_log(root, "backup delete requested")
    _remount_recovery_image(volume, root, "rw")

    delete_targets = ("images", "metadata", "logs", "state", "manifests", "hashes")
    delete_files = ("recovery-manifest.json", "recovery-manifest.sha256")

    try:
        for relative in delete_targets:
            path = root / relative
            if path.exists():
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()

        for relative in delete_files:
            path = root / relative
            if path.is_file():
                path.unlink()

        state = RecoveryState()
        state.current_stage = "NO_VALID_RECOVERY_IMAGE"
        state.restore_success = False
        state.rollback_required = False
        state.restore_in_progress = False
        state.last_failure_reason = None
        save_recovery_state(root, state)
        _write_audit_log(root, "backup delete completed")
        return {
            "status": "COMPLETED",
            "message": "Backup data deleted (NO_VALID_RECOVERY_IMAGE).",
            "recovery_root": str(root),
        }
    finally:
        try:
            _remount_recovery_image(volume, root, "ro")
        except RuntimeError:
            pass


def clear_restore_failure_lock() -> Dict[str, Any]:
    require_linux()
    _require_root()

    volume, root = _require_mounted_recovery_image()
    _write_audit_log(root, "restore failure lock clear requested")
    _remount_recovery_image(volume, root, "rw")

    try:
        state = load_recovery_state(root)
        previous = {
            "rollback_required": state.rollback_required,
            "restore_in_progress": state.restore_in_progress,
            "current_stage": state.current_stage,
            "last_failure_reason": state.last_failure_reason,
        }
        state.rollback_required = False
        state.restore_in_progress = False
        state.restore_success = False
        state.current_stage = "idle"
        state.last_failure_reason = None
        state.auto_retry_allowed = False
        save_recovery_state(root, state)
        _write_audit_log(root, "restore failure lock cleared")
        return {
            "status": "COMPLETED",
            "message": "Restore failure lock cleared.",
            "backup_preserved": True,
            "previous_state": previous,
            "recovery_root": str(root),
        }
    finally:
        try:
            _remount_recovery_image(volume, root, "ro")
        except RuntimeError:
            pass


def _cmd_reset_recovery_image(_args: argparse.Namespace) -> int:
    try:
        root = reset_recovery_image_for_backup()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print({"status": "COMPLETED", "recovery_root": str(root)})
    return 0


def _cmd_run_backup(_args: argparse.Namespace) -> int:
    try:
        payload = run_backup_apply()
    except (ConfirmationRequiredError, Exception) as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print(payload)
    return 0 if payload.get("status") == "COMPLETED" else 1


def _cmd_plan_backup(_args: argparse.Namespace) -> int:
    try:
        payload = run_backup_plan()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print({"status": "COMPLETED", "plan": payload})
    return 0


def _cmd_plan_admin_backup(_args: argparse.Namespace) -> int:
    try:
        payload = run_admin_backup_plan()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print({"status": "COMPLETED", "plan": payload})
    return 0


def _cmd_run_admin_backup(_args: argparse.Namespace) -> int:
    try:
        payload = run_admin_backup_apply()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print(payload)
    return 0 if payload.get("status") == "COMPLETED" else 1


def _cmd_plan_windows_extend(_args: argparse.Namespace) -> int:
    try:
        payload = run_windows_extend_plan()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print({"status": "COMPLETED", "plan": payload})
    return 0


def _cmd_run_windows_extend(_args: argparse.Namespace) -> int:
    try:
        payload = run_windows_extend_apply()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print(payload)
    return 0 if payload.get("status") == "COMPLETED" else 1


def _cmd_check_restore(_args: argparse.Namespace) -> int:
    try:
        payload = run_restore_check()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print(payload)
    return 0


def _cmd_run_restore(args: argparse.Namespace) -> int:
    try:
        payload = run_restore_apply(
            args.phrase,
            compatible_restore=bool(args.compatible_restore),
            progress_callback=_emit_progress,
        )
    except (ConfirmationRequiredError, Exception) as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print(payload)
    result = payload.get("result") or {}
    success_statuses = {"COMPLETED", "COMPLETED_NEEDS_WINDOWS_CHECK"}
    return 0 if payload.get("status") in success_statuses and result.get("success", True) else 1


def _cmd_delete_backup(_args: argparse.Namespace) -> int:
    try:
        payload = delete_backup_data()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print(payload)
    return 0


def _cmd_clear_restore_failure_lock(_args: argparse.Namespace) -> int:
    try:
        payload = clear_restore_failure_lock()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    _json_print(payload)
    return 0


def _find_windows_boot_id(efibootmgr_output: str) -> Optional[str]:
    for line in efibootmgr_output.splitlines():
        if WINDOWS_BOOT_MANAGER_LABEL not in line:
            continue
        match = re.match(r"^Boot([0-9A-Fa-f]{4})[*\s]", line)
        if match:
            return match.group(1).upper()
    return None


def reboot_to_windows() -> Dict[str, Any]:
    require_linux()
    firmware = run_readonly(["efibootmgr", "-v"])
    if firmware.returncode != 0:
        return {
            "status": "FAILED",
            "reason": firmware.stderr.strip() or "efibootmgr -v failed",
        }

    windows_boot_id = _find_windows_boot_id(firmware.stdout)
    if not windows_boot_id:
        return {
            "status": "FAILED",
            "reason": "Windows Boot Manager firmware entry not found",
        }

    bootnext = run_command(
        ["efibootmgr", "-n", windows_boot_id],
        dry_run=False,
        confirmed=True,
    )
    if bootnext.returncode != 0:
        return {
            "status": "FAILED",
            "reason": bootnext.stderr.strip() or f"failed to set BootNext={windows_boot_id}",
            "windows_boot_id": windows_boot_id,
        }

    _json_print(
        {
            "status": "COMPLETED",
            "message": "BootNext set to Windows Boot Manager; rebooting.",
            "windows_boot_id": windows_boot_id,
        }
    )
    subprocess.Popen(["systemctl", "reboot", "--no-wall"])
    return {
        "status": "COMPLETED",
        "message": "BootNext set to Windows Boot Manager; rebooting.",
        "windows_boot_id": windows_boot_id,
    }


def reboot_pc() -> Dict[str, Any]:
    require_linux()
    payload = {
        "status": "COMPLETED",
        "message": "System reboot requested.",
    }
    _json_print(payload)
    subprocess.Popen(["systemctl", "reboot", "--no-wall"])
    return payload


def _cmd_reboot_windows(_args: argparse.Namespace) -> int:
    try:
        payload = reboot_to_windows()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    if payload.get("status") != "COMPLETED":
        _json_print(payload)
        return 1
    return 0


def _cmd_reboot_pc(_args: argparse.Namespace) -> int:
    try:
        payload = reboot_pc()
    except Exception as exc:
        _json_print({"status": "FAILED", "reason": str(exc)})
        return 1
    if payload.get("status") != "COMPLETED":
        _json_print(payload)
        return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    setup_logging()
    _require_root()
    if (
        os.environ.get(SYSTEMD_RUN_CHILD_ENV) != "1"
        and raw_argv
        and raw_argv[0] != "check-restore"
    ):
        return _run_via_transient_root_service(raw_argv)
    parser = argparse.ArgumentParser(description="Recoverix privileged runtime helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reset_parser = subparsers.add_parser(
        "reset-recovery-image",
        help="Format or clear RECOVERY_IMAGE for a fresh backup",
    )
    reset_parser.set_defaults(handler=_cmd_reset_recovery_image)

    backup_parser = subparsers.add_parser(
        "run-backup",
        help="Run the full backup apply path as root",
    )
    backup_parser.set_defaults(handler=_cmd_run_backup)

    plan_backup_parser = subparsers.add_parser(
        "plan-backup",
        help="Run the backup dry-run plan path as root",
    )
    plan_backup_parser.set_defaults(handler=_cmd_plan_backup)

    plan_admin_backup_parser = subparsers.add_parser(
        "plan-admin-backup",
        help="Deprecated: administrator compact backup is disabled",
    )
    plan_admin_backup_parser.set_defaults(handler=_cmd_plan_admin_backup)

    admin_backup_parser = subparsers.add_parser(
        "run-admin-backup",
        help="Deprecated: administrator compact backup is disabled",
    )
    admin_backup_parser.set_defaults(handler=_cmd_run_admin_backup)

    plan_windows_extend_parser = subparsers.add_parser(
        "plan-windows-extend",
        help="Plan Windows partition boundary preparation for restore",
    )
    plan_windows_extend_parser.set_defaults(handler=_cmd_plan_windows_extend)

    run_windows_extend_parser = subparsers.add_parser(
        "run-windows-extend",
        help="Apply Windows partition boundary preparation for restore",
    )
    run_windows_extend_parser.set_defaults(handler=_cmd_run_windows_extend)

    check_restore_parser = subparsers.add_parser(
        "check-restore",
        help="Run restore validation/planning as root",
    )
    check_restore_parser.set_defaults(handler=_cmd_check_restore)

    run_restore_parser = subparsers.add_parser(
        "run-restore",
        help="Run the full destructive restore path as root",
    )
    run_restore_parser.add_argument(
        "--phrase",
        required=True,
        help="Exact restore confirmation phrase",
    )
    run_restore_parser.add_argument(
        "--compatible-restore",
        action="store_true",
        help="Allow disk replacement/hard-copy restore when compatibility checks pass",
    )
    run_restore_parser.set_defaults(handler=_cmd_run_restore)

    delete_backup_parser = subparsers.add_parser(
        "delete-backup",
        help="Delete backup contents from RECOVERY_IMAGE",
    )
    delete_backup_parser.set_defaults(handler=_cmd_delete_backup)

    clear_restore_lock_parser = subparsers.add_parser(
        "clear-restore-failure-lock",
        help="Clear restore failure lock while preserving backup data",
    )
    clear_restore_lock_parser.set_defaults(handler=_cmd_clear_restore_failure_lock)

    reboot_windows_parser = subparsers.add_parser(
        "reboot-windows",
        help="Set Windows Boot Manager as next boot target and reboot",
    )
    reboot_windows_parser.set_defaults(handler=_cmd_reboot_windows)

    reboot_pc_parser = subparsers.add_parser(
        "reboot-pc",
        help="Reboot the PC without changing the firmware next boot target",
    )
    reboot_pc_parser.set_defaults(handler=_cmd_reboot_pc)

    args = parser.parse_args(raw_argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())

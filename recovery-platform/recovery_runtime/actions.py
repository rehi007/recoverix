"""Menu action wrappers with validation, confirmation, and safe error handling."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, List, Optional

from backup_engine.backup_planner import create_backup_plan
from backup_engine.admin_backup import backup_type_label
from backup_engine.manifest import load_recovery_manifest
from backup_engine.run_backup import plan_backup_run
from backup_engine.write_guard import WriteGuard
from common.command import run_command
from common.errors import (
    BitLockerActiveError,
    ConfirmationRequiredError,
    InvalidConfirmationPhraseError,
    RestoreEnvironmentError,
    RestoreSafetyError,
)
from common.logger import get_logger
from partition_manager.windows_extend import plan_windows_partition_extend
from recovery_runtime.admin_bridge import (
    helper_available,
    run_runtime_admin_json,
    run_runtime_admin_streaming_json,
)
from restore_engine.confirmation import RESTORE_CONFIRMATION_PHRASE
from restore_engine.restore_executor import RestoreExecutor, build_execution_context
from restore_engine.restore_safety import authorize_restore_execution
from restore_engine.restore_state import (
    RecoveryState,
    load_recovery_state,
    logs_dir,
    save_recovery_state,
)
from recovery_runtime.runtime_context import RuntimeContext
from recovery_runtime.ui_helpers import (
    BACKUP_CONFIRMATION_PHRASE,
    append_error_log,
    format_bytes,
    prompt_yes_no,
    render_log_menu,
    suppress_tty_input,
)

logger = get_logger(__name__)
BOOTSTRAP_LOG_PATH = Path("/tmp/recovery-runtime-bootstrap.log")
SECTION_RULE = "=" * 60
_CLEAR_SCREEN = "\033[H\033[2J\033[3J"
_KV_LABEL_WIDTH = 18
UNSUPPORTED_BACKUP_TYPES = {"admin", "admin_compact", "compact", "compact_admin"}
UNSUPPORTED_BACKUP_REASON = (
    "Unsupported backup image detected. Delete this backup image and create a new standard backup."
)

LOG_FILES = (
    "restore.log",
    "rollback.log",
    "error.log",
    "integrity.log",
    "boot.log",
)

DELETE_TARGETS = (
    "images",
    "metadata",
    "logs",
    "state",
)

DELETE_FILES = (
    "recovery-manifest.json",
    "recovery-manifest.sha256",
)


def _safe_action(
    ctx: RuntimeContext,
    *,
    action_name: str,
    operation: Callable[[], str],
) -> str:
    try:
        return operation()
    except (ConfirmationRequiredError, InvalidConfirmationPhraseError) as exc:
        msg = str(exc)
        _log_ui_error(ctx, f"{action_name}: {msg}")
        return msg
    except (RestoreSafetyError, RestoreEnvironmentError, BitLockerActiveError) as exc:
        msg = str(exc)
        _log_ui_error(ctx, f"{action_name}: {msg}")
        return msg
    except Exception as exc:
        logger.exception("%s failed", action_name)
        msg = f"{action_name} failed: {exc}"
        _log_ui_error(ctx, msg)
        return msg


def _log_ui_error(ctx: RuntimeContext, message: str) -> None:
    if ctx.recovery_root:
        append_error_log(ctx.recovery_root, message)
    ctx.runtime_state.last_message = message


def _clear_screen() -> None:
    try:
        sys.stdout.write(_CLEAR_SCREEN)
        sys.stdout.flush()
    except Exception:
        pass


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value)


def _kv_line(label: str, value: Any) -> str:
    display = _display_value(value)
    return f"    {label:<{_KV_LABEL_WIDTH}}: {display}"


def _section_lines(title: str, rows: list[tuple[str, Any]]) -> list[str]:
    if not rows:
        return []
    lines = [f" {title}"]
    lines.extend(_kv_line(label, value) for label, value in rows)
    return lines


def _split_status_line(line: str) -> tuple[str, str]:
    if ":" not in line:
        return line.strip(), ""
    label, value = line.split(":", 1)
    return label.strip(), value.strip()


def show_status_action(ctx: RuntimeContext) -> str:
    def _run() -> str:
        ctx.refresh()
        status_rows = [_split_status_line(line) for line in ctx.status_lines]
        volume_rows: list[tuple[str, Any]] = []
        recovery_rows: list[tuple[str, Any]] = []
        boot_rows: list[tuple[str, Any]] = []

        volume_labels = {"Recovery Image", "Recovery Linux"}
        boot_labels = {"Windows Boot Mgr", "Secure Boot", "BitLocker", "BootOrder"}
        for label, value in status_rows:
            if label in volume_labels:
                volume_rows.append((label, value))
            elif label in boot_labels:
                boot_rows.append((label, value))
            else:
                recovery_rows.append((label, value))

        menu_rows: list[tuple[str, Any]] = []
        if ctx.menu.backup_reason:
            menu_rows.append(("Backup menu", _menu_state_text(False, ctx.menu.backup_reason)))
        else:
            menu_rows.append(("Backup menu", _menu_state_text(True, None)))
        if ctx.menu.restore_reason:
            menu_rows.append(("Restore menu", _menu_state_text(False, ctx.menu.restore_reason)))
        else:
            menu_rows.append(("Restore menu", _menu_state_text(True, None)))
        if ctx.menu.backup_warning:
            menu_rows.append(("Backup warning", ctx.menu.backup_warning))
        if ctx.firmware_state:
            boot_plan = None
            try:
                from boot_manager.bootorder_planner import plan_bootorder_recovery

                boot_plan = plan_bootorder_recovery(ctx.firmware_state, dry_run=True)
            except Exception:
                boot_plan = None
            if boot_plan:
                menu_rows.append(("BootOrder plan", boot_plan.status))

        body_lines: list[str] = []
        body_lines.extend(_section_lines("Runtime volumes", volume_rows))
        if body_lines:
            body_lines.append("")
        body_lines.extend(_section_lines("Recovery state", recovery_rows))
        if body_lines:
            body_lines.append("")
        body_lines.extend(_section_lines("Boot status", boot_rows))
        if body_lines:
            body_lines.append("")
        body_lines.extend(_section_lines("Menu state", menu_rows))
        body = "\n".join(body_lines).rstrip()
        return (
            f"{SECTION_RULE}\n"
            " System Status\n"
            f"{SECTION_RULE}\n"
            " Current runtime detection and recovery readiness.\n"
            "\n"
            f"{body}"
        )

    return _safe_action(ctx, action_name="status", operation=_run)


def _print_section_header(title: str, description: Optional[str] = None) -> None:
    print()
    print(SECTION_RULE)
    print(f" {title}")
    print(SECTION_RULE)
    if description:
        print(f" {description}")
        print()
    sys.stdout.flush()


def _format_section_message(title: str, description: Optional[str], message: str) -> str:
    lines = [SECTION_RULE, f" {title}", SECTION_RULE]
    if description:
        lines.extend([f" {description}", ""])
    lines.extend(_section_lines("Status", [("message", message)]))
    return "\n".join(lines)


def run_backup_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if not ctx.menu.backup_executable:
            return _format_section_message(
                "Create Recovery Backup",
                "Backup is not available right now.",
                f"Backup unavailable: {ctx.menu.backup_reason or 'disabled'}",
            )
        if ctx.recovery_root is None:
            return _format_section_message(
                "Create Recovery Backup",
                "Backup is not available right now.",
                "RECOVERY_IMAGE is not mounted.",
            )

        with _temporary_workdir():
            plan = create_backup_plan()
            dry_plan = _load_backup_plan_for_ui()

            def _print_backup_summary() -> None:
                _print_section_header(
                    "Create Recovery Backup",
                    "Review the items below, then continue.",
                )
                plan_rows: list[tuple[str, Any]] = [("status", plan.status)]
                if plan.reason:
                    plan_rows.append(("reason", plan.reason))
                _print_section("Backup plan", plan_rows)

                estimate_rows: list[tuple[str, Any]] = []
                if dry_plan.estimated_required_bytes:
                    estimate_rows.append(
                        (
                            "estimated size",
                            f"{format_bytes(dry_plan.estimated_required_bytes)} "
                            f"({dry_plan.estimated_required_gb} GiB planned)",
                        )
                    )
                if dry_plan.estimation_method:
                    estimate_rows.append(("estimation method", dry_plan.estimation_method))
                if dry_plan.estimated_used_bytes:
                    estimate_rows.append(
                        ("windows usage", format_bytes(dry_plan.estimated_used_bytes))
                    )
                if dry_plan.estimation_warning:
                    estimate_rows.append(("warning", dry_plan.estimation_warning))
                if dry_plan.reason:
                    estimate_rows.append(("reason", dry_plan.reason))
                _print_section("Storage estimate", estimate_rows)

                image_rows: list[tuple[str, Any]] = []
                if dry_plan.recovery_image_free_bytes is not None:
                    image_rows.append(
                        ("free space", format_bytes(dry_plan.recovery_image_free_bytes))
                    )
                if ctx.recovery_image_partition and ctx.recovery_image_partition.size:
                    image_rows.append(
                        ("partition size", format_bytes(ctx.recovery_image_partition.size))
                    )
                if ctx.menu.backup_warning:
                    image_rows.append(("warning", ctx.menu.backup_warning))
                _print_section("Recovery image", image_rows)
                sys.stdout.flush()

            _print_backup_summary()
            sys.stdout.flush()

            print()
            print("No valid recovery image exists.")
            print("RECOVERY_IMAGE must be initialized before creating a new backup.")
            print()
            sys.stdout.flush()
            if not prompt_yes_no(
                "Continue?",
                input_func=input_func,
            ):
                _clear_screen()
                _print_backup_summary()
                return "Backup canceled.\nReturning to main menu."

            _clear_screen()
            _print_backup_summary()
            print()
            print("WARNING: All data stored in RECOVERY_IMAGE will be permanently erased.")
            print("This action cannot be undone.")
            print()
            sys.stdout.flush()
            if not prompt_yes_no(
                "Initialize RECOVERY_IMAGE now?",
                input_func=input_func,
            ):
                _clear_screen()
                _print_backup_summary()
                return "Initialization canceled.\nReturning to main menu."

            formatted_root = _format_recovery_image_for_backup(ctx)
            ctx.refresh()
            plan = create_backup_plan()
            dry_plan = _load_backup_plan_for_ui()
            if not dry_plan.can_backup or dry_plan.status != "PLANNED":
                reason = dry_plan.reason or dry_plan.status
                if (
                    dry_plan.recovery_image_free_bytes is not None
                    and dry_plan.estimated_required_bytes
                    and dry_plan.recovery_image_free_bytes < dry_plan.estimated_required_bytes
                ):
                    return (
                        "Backup unavailable: insufficient recovery image space "
                        f"(required {format_bytes(dry_plan.estimated_required_bytes)}, "
                        f"free {format_bytes(dry_plan.recovery_image_free_bytes)})"
                    )
                return f"Backup unavailable: {reason}"

            _clear_screen()
            _print_backup_summary()
            print()
            print("RECOVERY_IMAGE has been initialized.")
            print()
            sys.stdout.flush()
            if not prompt_yes_no("Proceed with backup?", input_func=input_func):
                _clear_screen()
                _print_backup_summary()
                return "Backup canceled.\nReturning to main menu."

            _clear_screen()
            _print_backup_summary()
            print()
            print("Backup running. This can take a while.")
            sys.stdout.flush()
            _reset_backup_progress_display()
            with suppress_tty_input():
                rc, payload, stderr = _run_backup_admin_json_streaming(
                    "run-backup",
                    progress_callback=_print_backup_progress,
            )
            ctx.refresh()
            if payload.get("status") == "COMPLETED":
                _clear_screen()
                _print_backup_summary()
                return "Recovery backup completed."
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return f"Recovery backup failed: {reason}"

    return _safe_action(ctx, action_name="backup", operation=_run)


def run_restore_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
    admin_mode: bool = False,
) -> str:
    def _run() -> str:
        if not ctx.menu.restore_executable:
            return _format_section_message(
                "Restore System",
                "Restore is not available right now.",
                f"Restore unavailable: {ctx.menu.restore_reason or 'disabled'}",
            )
        if ctx.recovery_root is None or ctx.current_disk is None:
            return _format_section_message(
                "Restore System",
                "Restore is not available right now.",
                "Required disk information is unavailable.",
            )

        ctx.refresh()
        plan = ctx.restore_plan
        compatible_restore = bool(getattr(plan, "compatible_restore", False))

        def _print_restore_summary() -> None:
            _print_section_header(
                "Restore System",
                "Review the items below, then continue.",
            )
            print(" Restore validation")
            if ctx.validation_result:
                _print_kv(
                    "validate_restore",
                    "PASS" if ctx.validation_result.allowed else "FAIL",
                )
                if ctx.validation_result.reason:
                    _print_kv("reason", ctx.validation_result.reason)
            if plan:
                _print_kv("restore_allowed", "yes" if plan.restore_allowed else "no")
                _print_kv("restore_mode", getattr(plan, "restore_mode", "standard"))
                _print_kv("EFI rollback", "yes" if ctx.efi_rollback_available else "no")
                if admin_mode:
                    _print_admin_backup_information(ctx, plan)
                _print_restore_plan_summary(plan)

            boot_lines = [line for line in ctx.status_lines if "Windows Boot" in line]
            if boot_lines:
                print()
                print(" Boot status")
                for line in boot_lines:
                    if ":" in line:
                        label, value = line.split(":", 1)
                        _print_kv(label.strip(), value.strip())
                    else:
                        print(f"  {line}")
            sys.stdout.flush()

        _print_restore_summary()

        print()
        if compatible_restore:
            print("This backup was created from a different disk identity.")
            print("The current disk passed restore safety checks.")
            print()
            print("This can happen after disk replacement or hard-copy migration.")
            print("Continuing will overwrite the current Windows system partition.")
            print()
            prompt = "Continue?"
        else:
            print("Restore will overwrite the current Windows system partition with the backup image.")
            print("All current system data on the target Windows partition will be permanently lost.")
            print("This action cannot be undone.")
            prompt = "Continue?"
        print()
        sys.stdout.flush()
        if not prompt_yes_no(prompt, input_func=input_func):
            _clear_screen()
            _print_restore_summary()
            return "Restore canceled.\nReturning to main menu."

        _clear_screen()
        _print_restore_summary()
        print()
        print("All data on the Windows system partition will be permanently erased.")
        print("Move or back up important files before continuing.")
        print()
        sys.stdout.flush()
        if not prompt_yes_no("Start system restore now?", input_func=input_func):
            _clear_screen()
            _print_restore_summary()
            return "Restore canceled.\nReturning to main menu."

        phrase = RESTORE_CONFIRMATION_PHRASE

        _clear_screen()
        _print_restore_summary()
        print()
        print("Restore running. Do not power off this computer.")
        sys.stdout.flush()
        _reset_restore_progress_display()
        _print_restore_progress(
            {
                "percent": 0,
                "message": "Starting system restore",
                "detail": "Starting system restore",
            }
        )

        if helper_available():
            restore_args = ["run-restore", "--phrase", phrase]
            if compatible_restore:
                restore_args.append("--compatible-restore")
            with suppress_tty_input():
                rc, payload, stderr = _run_restore_admin_json_streaming(
                    *restore_args,
                    progress_callback=_print_restore_progress,
                )
            ctx.refresh()
            result = payload.get("result") or {}
            status = payload.get("status")
            if status in ("COMPLETED", "COMPLETED_NEEDS_WINDOWS_CHECK") and result.get("success", True):
                _clear_screen()
                _print_restore_summary()
                if status == "COMPLETED_NEEDS_WINDOWS_CHECK":
                    return (
                        "System restore completed.\n"
                        "Windows filesystem check is required.\n"
                        f"{result.get('reason') or payload.get('reason') or ''}".rstrip()
                    )
                return "System restore completed."
            reason = str(
                payload.get("reason")
                or result.get("reason")
                or stderr
                or payload.get("status")
                or rc
            )
            return f"System restore failed: {reason}"

        safety = authorize_restore_execution(
            apply=True,
            confirmed=True,
            confirmation_phrase=phrase,
            recovery_root=ctx.recovery_root,
            compatible_restore=compatible_restore,
        )
        if not safety.allowed:
            return safety.reason or "restore not authorized"

        exec_ctx = build_execution_context(
            safety,
            confirmed=True,
            recovery_root=ctx.recovery_root,
            runtime_state=ctx.runtime_state,
            progress_callback=_print_restore_progress,
            prevalidated=True,
        )
        with suppress_tty_input():
            result = RestoreExecutor(exec_ctx).execute()
        ctx.refresh()
        if result.success:
            _clear_screen()
            _print_restore_summary()
            if result.status == "COMPLETED_NEEDS_WINDOWS_CHECK":
                return (
                    "System restore completed.\n"
                    "Windows filesystem check is required.\n"
                    f"{result.reason or ''}".rstrip()
                )
            return "System restore completed."
        return f"System restore failed: {result.reason or result.status}"

    return _safe_action(ctx, action_name="restore", operation=_run)


def _print_admin_backup_information(ctx: RuntimeContext, plan: Any) -> None:
    manifest = _load_backup_manifest_for_ui(ctx.recovery_root)
    backup_type = (
        manifest.get("backup_type")
        or manifest.get("backup_mode")
        or _dict_get(getattr(plan, "recovery_image", None), "backup_type")
        or _dict_get(getattr(plan, "recovery_image", None), "backup_mode")
        or "standard"
    )
    rows: list[tuple[str, Any]] = [("backup type", _display_backup_type(backup_type))]

    baseline = (
        manifest.get("restore_baseline_bytes")
        or manifest.get("compact_baseline_bytes")
        or manifest.get("source_used_bytes")
        or _dict_get(getattr(plan, "recovery_image", None), "restore_baseline_bytes")
    )
    if baseline not in (None, ""):
        rows.append(("restore baseline", _format_restore_size(baseline)))
    rows.append(("manifest", "found" if manifest else "unknown"))
    _print_section("Backup information", rows)


def _display_backup_type(value: Any) -> str:
    normalized = str(value or "standard").strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    if normalized in UNSUPPORTED_BACKUP_TYPES:
        return "unsupported backup image"
    return backup_type_label(value)


def boot_recovery_status_action(ctx: RuntimeContext) -> str:
    def _run() -> str:
        ctx.refresh()
        boot_rows: list[tuple[str, Any]] = []
        recovery_rows: list[tuple[str, Any]] = []
        for line in ctx.status_lines:
            label, value = _split_status_line(line)
            if label in {"Windows Boot Mgr", "Secure Boot", "BitLocker", "BootOrder"}:
                boot_rows.append((label, value))
            elif label in {
                "Recovery Image",
                "Recovery Linux",
                "Valid backup",
                "Incomplete mark",
                "restore_allowed",
                "restore_mode",
            }:
                recovery_rows.append((label, value))

        if ctx.firmware_state:
            try:
                from boot_manager.bootorder_planner import plan_bootorder_recovery

                boot_plan = plan_bootorder_recovery(ctx.firmware_state, dry_run=True)
                boot_rows.append(("BootOrder plan", boot_plan.status))
                if boot_plan.reason:
                    boot_rows.append(("BootOrder reason", boot_plan.reason))
            except Exception as exc:
                boot_rows.append(("BootOrder plan", f"unknown ({exc})"))

        body_lines: list[str] = []
        body_lines.extend(_section_lines("Recovery status", recovery_rows))
        if body_lines:
            body_lines.append("")
        body_lines.extend(_section_lines("Boot status", boot_rows))
        body = "\n".join(body_lines).rstrip()
        return (
            f"{SECTION_RULE}\n"
            " Boot/Recovery Status\n"
            f"{SECTION_RULE}\n"
            " Show administrator boot and recovery readiness.\n"
            "\n"
            f"{body}"
        )

    return _safe_action(ctx, action_name="boot_recovery_status", operation=_run)


def _load_windows_extend_plan_for_ui(recovery_root: Optional[Path]) -> Any:
    if helper_available():
        rc, payload, stderr = run_runtime_admin_json("plan-windows-extend")
        plan_payload = payload.get("plan")
        if rc == 0 and payload.get("status") == "COMPLETED" and isinstance(plan_payload, dict):
            return SimpleNamespace(**plan_payload)
        logger.warning(
            "privileged Windows extend plan failed; falling back to local estimate: %s",
            payload.get("reason") or stderr or rc,
        )
    return plan_windows_partition_extend(recovery_root=recovery_root)


def _print_windows_extend_summary(plan: Any) -> None:
    _print_section_header(
        "Restore Partition Preparation",
        "Check Windows partition boundaries for partclone restore.",
    )
    status_rows: list[tuple[str, Any]] = [
        ("status", _attr(plan, "status", "UNKNOWN")),
        ("execution", "available" if _attr(plan, "can_extend", False) else "disabled"),
    ]
    if _attr(plan, "reason"):
        status_rows.append(("reason", _attr(plan, "reason")))
    _print_section("Status", status_rows)

    partition_rows: list[tuple[str, Any]] = []
    if _attr(plan, "windows_partition"):
        partition_rows.append(("Windows partition", _attr(plan, "windows_partition")))
    if _attr(plan, "current_size_bytes"):
        partition_rows.append(("current size", format_bytes(int(_attr(plan, "current_size_bytes")))))
    if _attr(plan, "required_size_bytes"):
        partition_rows.append(("required size", format_bytes(int(_attr(plan, "required_size_bytes")))))
    if _attr(plan, "restore_margin_bytes"):
        partition_rows.append(("restore margin", format_bytes(int(_attr(plan, "restore_margin_bytes")))))
    if _attr(plan, "available_after_bytes"):
        partition_rows.append(("available after C", format_bytes(int(_attr(plan, "available_after_bytes")))))
    if _attr(plan, "target_size_bytes"):
        partition_rows.append(("target size", format_bytes(int(_attr(plan, "target_size_bytes")))))
    if _attr(plan, "next_partition"):
        partition_rows.append(("next partition", _attr(plan, "next_partition")))
    _print_section("Partition plan", partition_rows)

    _print_section(
        "Safety policy",
        [
            ("partition move", "never"),
            ("free space", "adjacent free space after the Windows partition only"),
            ("NTFS resize", "not performed"),
            ("BitLocker", _attr(plan, "bitlocker", "UNKNOWN")),
            ("GPT backup", "saved before changes"),
        ],
    )
    sys.stdout.flush()


def windows_partition_extend_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        plan = _load_windows_extend_plan_for_ui(ctx.recovery_root)
        _print_windows_extend_summary(plan)
        if _attr(plan, "status") == "READY":
            return (
                "Windows partition is already large enough for restore.\n\n"
                "Run system restore again.\n\n"
                "Returning to administrator menu."
            )
        if not _attr(plan, "can_extend", False):
            reason = _attr(plan, "reason", "not available")
            return (
                "Windows partition cannot be prepared safely.\n\n"
                f"{reason}\n\n"
                "Returning to administrator menu."
            )

        print()
        print("This operation extends only the Windows partition boundary.")
        print("It does not resize the NTFS filesystem internally.")
        print("It does not move partitions.")
        print()
        print("Run system restore again after this completes.")
        print()
        sys.stdout.flush()
        if not prompt_yes_no("Continue?", input_func=input_func):
            _clear_screen()
            _print_windows_extend_summary(plan)
            return "Restore partition preparation canceled.\nReturning to administrator menu."

        _clear_screen()
        _print_windows_extend_summary(plan)
        print()
        print("Preparing restore partition. Do not power off this computer.")
        sys.stdout.flush()

        if not helper_available():
            return "Restore partition preparation failed: privileged helper is unavailable."

        rc, payload, stderr = run_runtime_admin_json("run-windows-extend")
        ctx.refresh()
        if payload.get("status") == "COMPLETED":
            return (
                "Windows partition boundary is ready for restore.\n\n"
                "Run system restore again.\n\n"
                "Returning to administrator menu."
            )
        if payload.get("status") == "PENDING_REBOOT":
            reason = str(payload.get("reason") or "The system has not detected the new partition size yet.")
            next_step = str(
                payload.get("next_step")
                or "Restart this computer, then run restore partition preparation again from administrator mode."
            )
            return (
                f"{reason}\n\n"
                "No internal NTFS filesystem change was applied.\n\n"
                f"{next_step}\n\n"
                "Returning to administrator menu."
            )
        reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
        return f"Restore partition preparation failed: {reason}"

    return _safe_action(ctx, action_name="windows_partition_extend", operation=_run)


def _dict_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def _load_backup_manifest_for_ui(recovery_root: Optional[Path]) -> dict[str, Any]:
    if recovery_root is None:
        return {}
    try:
        manifest = load_recovery_manifest(recovery_root)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _menu_state_text(enabled: bool, reason: Optional[str]) -> str:
    if enabled:
        return "executable"
    return f"disabled ({reason or 'not available'})"


def _print_restore_plan_summary(plan: Any, *, efi_rollback_available: Optional[bool] = None) -> None:
    target_disk = getattr(plan, "target_disk", None) or {}
    metadata = target_disk.get("metadata") if isinstance(target_disk, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}

    if target_disk:
        print()
        print(" Target disk")
        disk_path = target_disk.get("disk_path") if isinstance(target_disk, dict) else None
        _print_kv("disk_path", disk_path)
        _print_kv("disk_model", metadata.get("disk_model"))
        _print_kv("disk_serial", metadata.get("disk_serial"))
        _print_kv("disk_size", _format_restore_size(metadata.get("disk_size")))
        _print_kv("disk_guid", _short_text(metadata.get("disk_guid")))
        _print_kv("windows_uuid", _short_text(metadata.get("windows_partition_uuid")))
        _print_kv("efi_uuid", _short_text(metadata.get("efi_partition_uuid")))

    target_partitions = getattr(plan, "target_partitions", None) or {}
    if target_partitions:
        print()
        print(" Target partitions")
        for name, info in target_partitions.items():
            if isinstance(info, dict):
                path = info.get("path") or "unknown"
                label = info.get("label")
                fstype = info.get("fstype")
                size = _format_restore_size(info.get("size"))
                extras = ", ".join(part for part in (fstype, label, size) if part)
                print(_kv_line(name, f"{path}{f' ({extras})' if extras else ''}"))
            else:
                print(_kv_line(name, info))

    compatibility = getattr(plan, "compatibility", None) or {}
    target_check = compatibility.get("target_disk") if isinstance(compatibility, dict) else None
    compat_details = target_check.get("details", {}) if isinstance(target_check, dict) else {}
    if compat_details and (
        compat_details.get("windows_target_smaller")
        or compat_details.get("mismatches")
    ):
        print()
        print(" Compatibility")
        method = "standard partclone restore"
        _print_kv("method", method)
        _print_kv("source NTFS", _format_restore_size(compat_details.get("source_ntfs_size_bytes")))
        _print_kv("target Windows", _format_restore_size(compat_details.get("target_windows_size_bytes")))
        _print_kv("used bytes", _format_restore_size(compat_details.get("source_used_bytes")))
        if compat_details.get("size_deficit_bytes"):
            _print_kv("size deficit", _format_restore_size(compat_details.get("size_deficit_bytes")))
        if compat_details.get("legacy_used_range_unknown"):
            _print_kv("used range", "not required for partclone size check")
        elif compat_details.get("domain_map_present"):
            _print_kv("used range", "verified by domain map")


def _print_kv(label: str, value: Any) -> None:
    print(_kv_line(label, value))


def _print_section(title: str, rows: list[tuple[str, Any]]) -> None:
    lines = _section_lines(title, rows)
    if not lines:
        return
    print()
    for line in lines:
        print(line)


def _short_text(value: Any, *, limit: int = 38) -> str:
    text = str(value or "unknown")
    if len(text) <= limit:
        return text
    keep = max(8, (limit - 3) // 2)
    return f"{text[:keep]}...{text[-keep:]}"


def _format_restore_size(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return format_bytes(int(value))
    except (TypeError, ValueError):
        return str(value)


def delete_backup_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if not ctx.menu.delete_executable:
            return _format_section_message(
                "Delete Backup",
                "Backup deletion is not available right now.",
                f"Backup deletion unavailable: {ctx.menu.delete_reason or 'disabled'}",
            )
        root = ctx.recovery_root
        if root is None:
            return _format_section_message(
                "Delete Backup",
                "Backup deletion is not available right now.",
                "RECOVERY_IMAGE is not mounted.",
            )

        def _print_delete_summary() -> None:
            _print_section_header(
                "Delete Backup",
                "Review the warning below, then confirm.",
            )
            _print_section(
                "Delete scope",
                [
                    ("targets", "images/, metadata/, logs/, state/, manifest files"),
                    ("policy", "local confirmation required"),
                ],
            )
            sys.stdout.flush()

        _print_delete_summary()
        sys.stdout.flush()

        if not prompt_yes_no("Delete backup data?", input_func=input_func):
            _clear_screen()
            _print_delete_summary()
            return "Backup deletion canceled.\nReturning to main menu."

        _clear_screen()
        _print_delete_summary()
        print()
        print("WARNING: This will permanently delete the current recovery backup image.")
        print("The system cannot be restored until a new backup is created.")
        print("This action cannot be undone.")
        print()
        sys.stdout.flush()
        if not prompt_yes_no("Delete backup data now?", input_func=input_func):
            _clear_screen()
            _print_delete_summary()
            return "Backup deletion canceled.\nReturning to main menu."

        if helper_available():
            rc, payload, stderr = run_runtime_admin_json("delete-backup")
            ctx.refresh()
            if payload.get("status") == "COMPLETED":
                _clear_screen()
                _print_delete_summary()
                return "Recovery backup deleted."
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return f"Backup deletion failed: {reason}"

        _write_audit_log(root, "backup delete requested")
        staging = root / ".delete_staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        try:
            paths = _collect_delete_paths(root)
            if not paths:
                return "No backup data found."
            staging.mkdir(parents=True, exist_ok=True)
            for path in paths:
                rel = path.relative_to(root)
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.copytree(path, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(path, dest)
            for path in paths:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.is_file():
                    path.unlink()
            _mark_no_valid_recovery_image(root)
            shutil.rmtree(staging, ignore_errors=True)
            ctx.refresh()
            _clear_screen()
            _print_delete_summary()
            return "Recovery backup deleted."
        except Exception as exc:
            logger.exception("backup delete failed; attempting rollback")
            _rollback_delete(staging, root)
            state = load_recovery_state(root)
            state.rollback_required = True
            state.last_failure_reason = str(exc)
            save_recovery_state(root, state)
            _write_audit_log(root, f"backup delete failed: {exc}")
            raise

    return _safe_action(ctx, action_name="delete_backup", operation=_run)


def clear_restore_failure_lock_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        root = ctx.recovery_root
        if root is None:
            return _format_section_message(
                "Clear Restore Failure Lock",
                "Restore failure lock cannot be cleared right now.",
                "RECOVERY_IMAGE is not mounted.",
            )

        def _print_clear_summary() -> None:
            _print_section_header(
                "Clear Restore Failure Lock",
                "Clear a previous restore failure lock without deleting backup data.",
            )
            state = load_recovery_state(root)
            _print_section(
                "Current lock state",
                [
                    ("rollback required", "yes" if state.rollback_required else "no"),
                    ("restore in progress", "yes" if state.restore_in_progress else "no"),
                    ("current stage", state.current_stage),
                    ("last failure", state.last_failure_reason or "none"),
                ],
            )
            _print_section(
                "Preserved data",
                [
                    ("backup image", "preserved"),
                    ("manifest", "preserved"),
                    ("logs", "preserved"),
                ],
            )
            sys.stdout.flush()

        _print_clear_summary()
        print()
        print("This will only clear the restore failure lock.")
        print("Backup image data will not be deleted.")
        print()
        sys.stdout.flush()
        if not prompt_yes_no("Clear restore failure lock?", input_func=input_func):
            _clear_screen()
            _print_clear_summary()
            return "Restore failure lock clear canceled.\nReturning to administrator menu."

        if helper_available():
            rc, payload, stderr = run_runtime_admin_json("clear-restore-failure-lock")
            ctx.refresh()
            if payload.get("status") == "COMPLETED":
                _clear_screen()
                _print_clear_summary()
                return (
                    "Restore failure lock cleared.\n"
                    "Backup image was not deleted.\n\n"
                    "Returning to administrator menu."
                )
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return f"Restore failure lock clear failed: {reason}"

        state = load_recovery_state(root)
        state.rollback_required = False
        state.restore_in_progress = False
        state.restore_success = False
        state.current_stage = "idle"
        state.last_failure_reason = None
        state.auto_retry_allowed = False
        save_recovery_state(root, state)
        ctx.refresh()
        _clear_screen()
        _print_clear_summary()
        return (
            "Restore failure lock cleared.\n"
            "Backup image was not deleted.\n\n"
            "Returning to administrator menu."
        )

    return _safe_action(ctx, action_name="clear_restore_failure_lock", operation=_run)


def show_logs_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if ctx.recovery_root is None:
            return _format_section_message(
                "Diagnostics Logs",
                "Logs are not available right now.",
                "RECOVERY_IMAGE is not mounted, so logs are unavailable.",
            )
        _clear_screen()
        _print_section_header(
            "Diagnostics Logs",
            "Open runtime and recovery logs.",
        )
        log_entries: list[str | tuple[str, Path]] = list(LOG_FILES)
        log_entries.append(("recovery-runtime-bootstrap.log", BOOTSTRAP_LOG_PATH))
        return render_log_menu(ctx.recovery_root, log_entries, input_func=input_func)

    return _safe_action(ctx, action_name="logs", operation=_run)


def reboot_to_windows_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if not helper_available():
            return _format_section_message(
                "Reboot to Windows",
                "Windows reboot is not available right now.",
                "Privileged runtime helper is missing.",
            )
        def _print_reboot_summary() -> None:
            _print_section_header(
                "Reboot to Windows",
                "The recovery menu will close.",
            )
            _print_section(
                "Reboot target",
                [
                    ("target", "Windows"),
                    ("action", "restart this computer and boot into Windows"),
                ],
            )
            sys.stdout.flush()

        _print_reboot_summary()
        print()
        if not prompt_yes_no("Continue?", input_func=input_func):
            _clear_screen()
            _print_reboot_summary()
            return "Reboot to Windows canceled.\nReturning to main menu."

        _clear_screen()
        _print_reboot_summary()
        print()
        print("Rebooting to Windows.")
        sys.stdout.flush()
        rc, payload, stderr = run_runtime_admin_json("reboot-windows")
        if rc == 0 and payload.get("status") == "COMPLETED":
            return str(payload.get("message") or "Rebooting to Windows.")
        reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
        return f"Reboot to Windows failed: {reason}"

    return _safe_action(ctx, action_name="reboot_windows", operation=_run)


def _load_backup_plan_for_ui() -> Any:
    if helper_available():
        rc, payload, stderr = run_runtime_admin_json("plan-backup")
        plan_payload = payload.get("plan")
        if rc == 0 and payload.get("status") == "COMPLETED" and isinstance(plan_payload, dict):
            return SimpleNamespace(**plan_payload)
        logger.warning(
            "privileged backup plan failed; falling back to local estimate: %s",
            payload.get("reason") or stderr or rc,
        )
    return plan_backup_run(WriteGuard(apply=False, confirmed=False))


def _collect_delete_paths(root: Path) -> List[Path]:
    paths: List[Path] = []
    for relative in DELETE_TARGETS:
        path = root / relative
        if path.exists():
            paths.append(path)
    for relative in DELETE_FILES:
        path = root / relative
        if path.is_file():
            paths.append(path)
    marker = root / "state" / "incomplete_backup"
    if marker.is_file():
        paths.append(marker)
    return paths


def _format_recovery_image_for_backup(ctx: RuntimeContext) -> Path:
    if ctx.recovery_root is None:
        raise RuntimeError("RECOVERY_IMAGE is unavailable")
    _write_audit_log(ctx.recovery_root, "backup format requested")
    rc, payload, stderr = _run_backup_admin_json("reset-recovery-image")
    if rc != 0 or payload.get("status") != "COMPLETED":
        reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
        raise RuntimeError(reason)
    recovery_root = payload.get("recovery_root")
    if not recovery_root:
        raise RuntimeError("backup helper did not return recovery_root")
    return Path(str(recovery_root))


class _temporary_workdir:
    def __init__(self, base: Path = Path("/tmp/recoverix-backup-work")) -> None:
        self._target = base
        self._previous: Optional[str] = None

    def __enter__(self) -> Path:
        self._target.mkdir(parents=True, exist_ok=True)
        self._previous = os.getcwd()
        os.chdir(self._target)
        return self._target

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._previous is not None:
            os.chdir(self._previous)


def _run_backup_admin_json(*args: str) -> tuple[int, dict[str, Any], str]:
    return run_runtime_admin_json(*args)


def _run_backup_admin_json_streaming(
    *args: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[int, dict[str, Any], str]:
    return run_runtime_admin_streaming_json(*args, on_progress=progress_callback)


def _run_restore_admin_json_streaming(
    *args: str,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[int, dict[str, Any], str]:
    return run_runtime_admin_streaming_json(*args, on_progress=progress_callback)


def _print_backup_progress(event: dict[str, Any]) -> None:
    message = str(event.get("message") or "").strip()
    if not message:
        return
    percent = event.get("percent")
    detail = str(event.get("detail") or "").strip()
    final = bool(event.get("final"))
    if final:
        if getattr(_print_backup_progress, "_inline_active", False):
            previous = getattr(_print_backup_progress, "_display_percent", None)
            if not isinstance(previous, int) or previous < 99:
                print("\rBackup progress:  99% | Finalizing backup metadata             ", end="")
                sys.stdout.flush()
                time.sleep(1.0)
            print("\rBackup progress: 100% | Recovery backup completed               ", end="")
            sys.stdout.flush()
            time.sleep(1.0)
            print(f"\r{'':<96}\r", end="")
        setattr(_print_backup_progress, "_display_percent", None)
        setattr(_print_backup_progress, "_inline_active", False)
        sys.stdout.flush()
        return
    if isinstance(percent, int):
        display_percent = _smoothed_backup_display_percent(percent, final=final)
        if display_percent is None:
            return
        suffix_text = detail or message
        suffix = f" | {suffix_text}" if suffix_text else ""
        end = "\n" if final else ""
        print(f"\rBackup progress: {display_percent:3d}%{suffix:<48}", end=end)
        setattr(_print_backup_progress, "_inline_active", not final)
        sys.stdout.flush()
        return

    if getattr(_print_backup_progress, "_inline_active", False):
        print()
        setattr(_print_backup_progress, "_inline_active", False)

    step = event.get("step")
    total = event.get("total")
    prefix = ""
    if isinstance(step, int) and isinstance(total, int) and total > 0:
        prefix = f"[{step}/{total}] "
    print(f"{prefix}{message}")
    sys.stdout.flush()


def _print_restore_progress(event: dict[str, Any]) -> None:
    message = str(event.get("message") or "").strip()
    if not message:
        return
    percent = event.get("percent")
    detail = str(event.get("detail") or "").strip()
    final = bool(event.get("final"))
    if final:
        if getattr(_print_restore_progress, "_inline_active", False):
            print(f"\r{'':<96}\r", end="")
        setattr(_print_restore_progress, "_display_percent", None)
        setattr(_print_restore_progress, "_inline_active", False)
        sys.stdout.flush()
        return
    if isinstance(percent, int):
        display_percent = _smoothed_restore_display_percent(percent, final=final)
        if display_percent is None:
            return
        suffix_text = detail or message
        suffix = f" | {suffix_text}" if suffix_text else ""
        end = "\n" if final else ""
        print(f"\rRestore progress: {display_percent:3d}%{suffix:<48}", end=end)
        setattr(_print_restore_progress, "_inline_active", not final)
        sys.stdout.flush()
        return

    if getattr(_print_restore_progress, "_inline_active", False):
        print()
        setattr(_print_restore_progress, "_inline_active", False)

    print(message)
    sys.stdout.flush()


def _reset_restore_progress_display() -> None:
    setattr(_print_restore_progress, "_display_percent", None)
    setattr(_print_restore_progress, "_display_time", time.monotonic())
    setattr(_print_restore_progress, "_inline_active", False)


def _reset_backup_progress_display() -> None:
    now = time.monotonic()
    setattr(_print_backup_progress, "_display_percent", None)
    setattr(_print_backup_progress, "_display_time", now)
    setattr(_print_backup_progress, "_display_start_time", now)
    setattr(_print_backup_progress, "_inline_active", False)


def _smoothed_backup_display_percent(target: int, *, final: bool) -> Optional[int]:
    target = max(0, min(100, int(target)))
    now = time.monotonic()
    previous = getattr(_print_backup_progress, "_display_percent", None)
    last_time = float(getattr(_print_backup_progress, "_display_time", now))

    if final:
        display = 100
    elif previous is None:
        display = target
    elif target <= previous:
        if now - last_time < 1.0:
            return None
        display = previous
    else:
        if now - last_time < 1.0:
            return None
        display = target

    setattr(_print_backup_progress, "_display_percent", display)
    setattr(_print_backup_progress, "_display_time", now)
    if final:
        setattr(_print_backup_progress, "_display_percent", None)
    return display


def _smoothed_restore_display_percent(target: int, *, final: bool) -> Optional[int]:
    target = max(0, min(100, int(target)))
    now = time.monotonic()
    previous = getattr(_print_restore_progress, "_display_percent", None)
    last_time = float(getattr(_print_restore_progress, "_display_time", now))

    if final:
        display = 100
    elif previous is None or target <= 1:
        display = target
    elif target == previous:
        if now - last_time < 1.0:
            return None
        display = previous
    elif target < previous:
        return None
    else:
        elapsed = max(0.0, now - last_time)
        max_step = min(5, int(elapsed * 1.0))
        if max_step <= 0:
            return None
        display = min(target, previous + max_step)

    setattr(_print_restore_progress, "_display_percent", display)
    setattr(_print_restore_progress, "_display_time", now)
    if final:
        setattr(_print_restore_progress, "_display_percent", None)
    return display


def _rollback_delete(staging: Path, root: Path) -> None:
    if not staging.is_dir():
        return
    for item in staging.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(staging)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
    for item in staging.iterdir():
        if item.is_dir():
            dest = root / item.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest, dirs_exist_ok=True)


def _mark_no_valid_recovery_image(root: Path) -> None:
    state = load_recovery_state(root)
    state.current_stage = "NO_VALID_RECOVERY_IMAGE"
    state.restore_success = False
    state.rollback_required = False
    state.restore_in_progress = False
    state.last_failure_reason = None
    save_recovery_state(root, state)


def _write_audit_log(root: Path, message: str) -> None:
    try:
        log_dir = logs_dir(root)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "audit.log"
        stamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | {message}\n")
    except OSError:
        pass

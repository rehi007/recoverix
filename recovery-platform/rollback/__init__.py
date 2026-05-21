"""Rollback and recovery point management."""

from __future__ import annotations

from typing import Any

__all__ = [
    "EfiRollbackResult",
    "FailureCounterState",
    "GptRollbackResult",
    "MAX_RESTORE_ATTEMPTS",
    "MAX_ROLLBACK_ATTEMPTS",
    "RECOVERYBOOT_FAILURE_THRESHOLD",
    "build_windows_first_boot_order",
    "detect_reboot_loop_risk",
    "is_retry_allowed",
    "is_rollback_retry_allowed",
    "load_failure_counter_state",
    "plan_windows_first_boot_commands",
    "record_failure",
    "record_recoveryboot_failure",
    "record_restore_failure_state",
    "resolve_gpt_backup_file",
    "rollback_efi",
    "rollback_gpt",
    "should_apply_windows_first_policy",
    "validate_bootnext_policy",
]


def __getattr__(name: str) -> Any:
    if name in {
        "EfiRollbackResult",
        "rollback_efi",
    }:
        from .efi_rollback import EfiRollbackResult, rollback_efi

        return {"EfiRollbackResult": EfiRollbackResult, "rollback_efi": rollback_efi}[name]
    if name in {
        "GptRollbackResult",
        "resolve_gpt_backup_file",
        "rollback_gpt",
    }:
        from .gpt_rollback import GptRollbackResult, resolve_gpt_backup_file, rollback_gpt

        return {
            "GptRollbackResult": GptRollbackResult,
            "resolve_gpt_backup_file": resolve_gpt_backup_file,
            "rollback_gpt": rollback_gpt,
        }[name]
    if name in {
        "FailureCounterState",
        "MAX_RESTORE_ATTEMPTS",
        "MAX_ROLLBACK_ATTEMPTS",
        "RECOVERYBOOT_FAILURE_THRESHOLD",
        "build_windows_first_boot_order",
        "detect_reboot_loop_risk",
        "is_retry_allowed",
        "is_rollback_retry_allowed",
        "load_failure_counter_state",
        "plan_windows_first_boot_commands",
        "record_failure",
        "record_recoveryboot_failure",
        "record_restore_failure_state",
        "should_apply_windows_first_policy",
        "validate_bootnext_policy",
    }:
        from . import failure_counter as fc

        return getattr(fc, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

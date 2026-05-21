"""Restore and RecoveryBoot failure tracking (no automatic retry)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from boot_manager.bootorder_planner import format_displayorder_command
from common.logger import get_logger
from restore_engine.restore_state import (
    RecoveryState,
    is_interrupted_restore,
    is_restore_in_progress,
    load_recovery_state,
    mark_restore_failed,
    save_recovery_state,
)

logger = get_logger(__name__)

MAX_RESTORE_ATTEMPTS = 1
MAX_ROLLBACK_ATTEMPTS = 1
RECOVERYBOOT_FAILURE_THRESHOLD = 3
RESTORE_STALE_SECONDS = 3600


@dataclass
class FailureCounterState:
    """Aggregated failure and rollback policy state."""

    failure_count: int = 0
    recoveryboot_failure_count: int = 0
    last_failure_reason: Optional[str] = None
    rollback_required: bool = False
    auto_retry_allowed: bool = False
    rollback_retry_allowed: bool = False
    last_stage: Optional[str] = None
    windows_first_required: bool = False
    reboot_loop_risk: bool = False
    interrupted_restore: bool = False
    restore_in_progress: bool = False
    boot_next_policy: str = "preserve"
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_count": self.failure_count,
            "recoveryboot_failure_count": self.recoveryboot_failure_count,
            "last_failure_reason": self.last_failure_reason,
            "rollback_required": self.rollback_required,
            "auto_retry_allowed": self.auto_retry_allowed,
            "rollback_retry_allowed": self.rollback_retry_allowed,
            "last_stage": self.last_stage,
            "windows_first_required": self.windows_first_required,
            "reboot_loop_risk": self.reboot_loop_risk,
            "interrupted_restore": self.interrupted_restore,
            "restore_in_progress": self.restore_in_progress,
            "boot_next_policy": self.boot_next_policy,
            "updated_at": self.updated_at,
        }


def is_retry_allowed(_state: Optional[FailureCounterState] = None) -> bool:
    """Automatic restore retry is forbidden by policy."""
    return False


def is_rollback_retry_allowed(_state: Optional[FailureCounterState] = None) -> bool:
    """Automatic rollback retry is forbidden by policy."""
    return False


def should_apply_windows_first_policy(state: RecoveryState) -> bool:
    """After three RecoveryBoot failures, prioritize Windows Boot Manager."""
    return state.recoveryboot_failure_count >= RECOVERYBOOT_FAILURE_THRESHOLD


def detect_reboot_loop_risk(state: RecoveryState) -> bool:
    """Elevated risk when RecoveryBoot repeatedly fails or restore was interrupted."""
    if should_apply_windows_first_policy(state):
        return True
    if is_interrupted_restore(state, stale_seconds=RESTORE_STALE_SECONDS):
        return True
    return False


def build_windows_first_boot_order(
    *,
    windows_boot_id: str,
    recovery_boot_id: Optional[str],
    current_order: List[str],
) -> List[str]:
    """Windows Boot Manager first; RecoveryBoot second; preserve remaining entries."""
    remaining = [
        identifier
        for identifier in current_order
        if identifier not in {windows_boot_id, recovery_boot_id}
    ]
    order: List[str] = [windows_boot_id]
    if recovery_boot_id:
        order.append(recovery_boot_id)
    order.extend(remaining)
    return order


def plan_windows_first_boot_commands(
    *,
    windows_boot_id: str,
    recovery_boot_id: Optional[str],
    current_order: List[str],
) -> List[str]:
    """Planned displayorder commands for Windows-first boot policy."""
    order = build_windows_first_boot_order(
        windows_boot_id=windows_boot_id,
        recovery_boot_id=recovery_boot_id,
        current_order=current_order,
    )
    return [format_displayorder_command(order)]


def validate_bootnext_policy(
    *,
    boot_next_policy: str,
    planned_commands: List[str],
) -> bool:
    """
    Reject BootNext overrides that abuse one-shot recovery boot.

    Policy: BootNext must be preserved; no command may force RecoveryBoot via BootNext.
    """
    if boot_next_policy != "preserve":
        return False
    forbidden_fragments = (
        "bootnext",
        "boot next",
    )
    for command in planned_commands:
        lowered = command.lower()
        if any(fragment in lowered for fragment in forbidden_fragments):
            return False
        if "recoveryboot" in lowered and "bootnext" in lowered:
            return False
    return True


def _snapshot_from_recovery_state(
    recovery_state: RecoveryState,
    *,
    stage: Optional[str] = None,
    reason: Optional[str] = None,
) -> FailureCounterState:
    return FailureCounterState(
        failure_count=recovery_state.failure_count,
        recoveryboot_failure_count=recovery_state.recoveryboot_failure_count,
        last_failure_reason=reason or recovery_state.last_failure_reason,
        rollback_required=recovery_state.rollback_required,
        auto_retry_allowed=False,
        rollback_retry_allowed=False,
        last_stage=stage or recovery_state.current_stage,
        windows_first_required=should_apply_windows_first_policy(recovery_state),
        reboot_loop_risk=detect_reboot_loop_risk(recovery_state),
        interrupted_restore=is_interrupted_restore(
            recovery_state,
            stale_seconds=RESTORE_STALE_SECONDS,
        ),
        restore_in_progress=is_restore_in_progress(recovery_state),
        boot_next_policy="preserve",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def record_restore_failure_state(
    recovery_root: Path,
    reason: str,
    *,
    stage: str,
    state_path: Optional[Path] = None,
) -> FailureCounterState:
    """Record restore failure in recovery_state.json (no auto retry)."""
    mark_restore_failed(recovery_root, reason, stage=stage, path=state_path)
    recovery_state = load_recovery_state(recovery_root, path=state_path)
    recovery_state.failure_count = min(
        recovery_state.failure_count + 1,
        MAX_RESTORE_ATTEMPTS,
    )
    recovery_state.auto_retry_allowed = False
    save_recovery_state(recovery_root, recovery_state, path=state_path)
    counter = _snapshot_from_recovery_state(
        recovery_state,
        stage=stage,
        reason=reason,
    )
    logger.warning(
        "restore failure state recorded (count=%s): %s",
        counter.failure_count,
        reason,
    )
    return counter


def record_failure(
    recovery_root: Path,
    reason: str,
    *,
    stage: str,
    state_path: Optional[Path] = None,
) -> FailureCounterState:
    """Backward-compatible alias for restore failure recording."""
    return record_restore_failure_state(
        recovery_root,
        reason,
        stage=stage,
        state_path=state_path,
    )


def record_recoveryboot_failure(
    recovery_root: Path,
    reason: str,
    *,
    stage: str = "recoveryboot_boot_failed",
    state_path: Optional[Path] = None,
) -> FailureCounterState:
    """Increment RecoveryBoot failure count and apply Windows-first policy when needed."""
    recovery_state = load_recovery_state(recovery_root, path=state_path)
    recovery_state.recoveryboot_failure_count += 1
    recovery_state.last_failure_reason = reason
    recovery_state.current_stage = stage
    recovery_state.rollback_required = True
    recovery_state.auto_retry_allowed = False
    if should_apply_windows_first_policy(recovery_state):
        recovery_state.last_successful_boot = "windows"
        logger.warning(
            "RecoveryBoot failure threshold reached (%s); Windows-first boot required",
            RECOVERYBOOT_FAILURE_THRESHOLD,
        )
    save_recovery_state(recovery_root, recovery_state, path=state_path)
    counter = _snapshot_from_recovery_state(recovery_state, stage=stage, reason=reason)
    logger.warning(
        "RecoveryBoot failure recorded (count=%s): %s",
        counter.recoveryboot_failure_count,
        reason,
    )
    return counter


def load_failure_counter_state(
    recovery_root: Path,
    *,
    state_path: Optional[Path] = None,
) -> FailureCounterState:
    """Load current failure counters and policy flags from recovery_state.json."""
    recovery_state = load_recovery_state(recovery_root, path=state_path)
    return _snapshot_from_recovery_state(recovery_state)


def mark_last_successful_boot(
    recovery_root: Path,
    boot_target: str,
    *,
    state_path: Optional[Path] = None,
) -> RecoveryState:
    """Record the last known good boot target (windows or recovery)."""
    recovery_state = load_recovery_state(recovery_root, path=state_path)
    recovery_state.last_successful_boot = boot_target
    save_recovery_state(recovery_root, recovery_state, path=state_path)
    return recovery_state

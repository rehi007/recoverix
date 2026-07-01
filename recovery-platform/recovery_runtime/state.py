"""Recovery runtime state and feature flags."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from recovery_runtime.discover import DiscoveredVolume
    from restore_engine.restore_state import RecoveryState


@dataclass
class RuntimeState:
    """Mutable runtime state for the recovery environment."""

    recovery_image: Optional["DiscoveredVolume"] = None
    recovery_linux: Optional["DiscoveredVolume"] = None
    all_volumes: List["DiscoveredVolume"] = field(default_factory=list)
    restore_enabled: bool = False
    backup_enabled: bool = False
    destructive_allowed: bool = False
    manifest_present: bool = False
    manifest_path: Optional[str] = None
    last_message: str = ""
    persisted: Optional["RecoveryState"] = None
    rollback_required: bool = False
    restore_in_progress: bool = False
    interrupted_restore: bool = False
    windows_first_required: bool = False
    reboot_loop_risk: bool = False
    recoveryboot_failure_count: int = 0

    def summary_lines(self) -> List[str]:
        lines = [
            f"Recovery Image : {self._format_volume(self.recovery_image)}",
            f"Recovery Linux : {self._format_volume(self.recovery_linux)}",
            f"Manifest       : {'found' if self.manifest_present else 'missing'}",
            f"Restore        : {'enabled' if self.restore_enabled else 'disabled'}",
            f"Backup         : {'enabled' if self.backup_enabled else 'disabled'}",
            f"Rollback       : {'required' if self.rollback_required else 'not required'}",
            f"Restore active : {'yes' if self.restore_in_progress else 'no'}",
            f"Interrupted    : {'yes' if self.interrupted_restore else 'no'}",
            f"Windows-first  : {'required' if self.windows_first_required else 'no'}",
            f"Reboot loop    : {'risk' if self.reboot_loop_risk else 'ok'}",
        ]
        if self.manifest_path:
            lines.append(f"Manifest path  : {self.manifest_path}")
        if self.persisted and self.persisted.last_failure_reason:
            lines.append(f"Last failure   : {self.persisted.last_failure_reason}")
        if self.persisted and self.persisted.current_stage:
            lines.append(f"Stage          : {self.persisted.current_stage}")
        if self.last_message:
            lines.append(f"Last message   : {self._summary_message(self.last_message)}")
        return lines

    @staticmethod
    def _format_volume(volume: Optional["DiscoveredVolume"]) -> str:
        if volume is None:
            return "not found"
        mount = volume.mountpoint or "(unmounted)"
        return f"{volume.path} label={volume.label!r} mount={mount}"

    @staticmethod
    def _summary_message(message: str) -> str:
        first_line = next(
            (
                line.strip()
                for line in message.splitlines()
                if line.strip() and not set(line.strip()) <= {"=", "-"}
            ),
            "",
        )
        if not first_line:
            return ""
        return first_line if len(first_line) <= 60 else f"{first_line[:57]}..."


def apply_persisted_recovery_state(
    runtime: RuntimeState,
    recovery_root: Path,
) -> RuntimeState:
    """Load recovery_state.json flags into runtime (read-only)."""
    from restore_engine.restore_state import (
        is_interrupted_restore,
        is_restore_in_progress,
        load_recovery_state,
    )
    from rollback.failure_counter import (
        RESTORE_STALE_SECONDS,
        detect_reboot_loop_risk,
        load_failure_counter_state,
        should_apply_windows_first_policy,
    )

    persisted = load_recovery_state(recovery_root)
    counter = load_failure_counter_state(recovery_root)
    runtime.persisted = persisted
    runtime.rollback_required = persisted.rollback_required
    runtime.restore_in_progress = is_restore_in_progress(persisted)
    runtime.interrupted_restore = is_interrupted_restore(
        persisted,
        stale_seconds=RESTORE_STALE_SECONDS,
    )
    runtime.windows_first_required = should_apply_windows_first_policy(persisted)
    runtime.reboot_loop_risk = detect_reboot_loop_risk(persisted)
    runtime.recoveryboot_failure_count = persisted.recoveryboot_failure_count

    if runtime.interrupted_restore:
        runtime.restore_enabled = False
        runtime.destructive_allowed = False
        runtime.last_message = "interrupted restore detected; restore disabled"
    elif runtime.rollback_required:
        runtime.restore_enabled = False
        runtime.last_message = "rollback required before another restore"
    elif runtime.reboot_loop_risk:
        runtime.restore_enabled = False
        runtime.last_message = "reboot loop prevention active (Windows-first required)"
    return runtime

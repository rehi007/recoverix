"""Polling-based firmware / BootOrder drift detection (Windows-side)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from boot_manager.boot_entry import FirmwareAnalysisResult

from windows_agent.logging_config import append_log, get_log_directory


@dataclass(frozen=True)
class FirmwareTelemetrySnapshot:
    """Minimal stable snapshot for drift detection (no ext4 / Recovery Image access)."""

    boot_order: Tuple[str, ...]
    recovery_identifier: Optional[str]
    windows_identifier: Optional[str]
    boot_next: Optional[str]

    @classmethod
    def from_analysis(cls, analysis: FirmwareAnalysisResult) -> "FirmwareTelemetrySnapshot":
        rid = analysis.recovery_boot.identifier if analysis.recovery_boot else None
        wid = analysis.windows_boot_manager.identifier if analysis.windows_boot_manager else None
        return cls(
            tuple(analysis.boot_order),
            rid,
            wid,
            analysis.boot_next,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "boot_order": list(self.boot_order),
            "recovery_identifier": self.recovery_identifier,
            "windows_identifier": self.windows_identifier,
            "boot_next": self.boot_next,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FirmwareTelemetrySnapshot":
        order = tuple(data.get("boot_order") or [])
        return cls(
            order,
            data.get("recovery_identifier"),
            data.get("windows_identifier"),
            data.get("boot_next"),
        )


def telemetry_state_path() -> Path:
    root = os.environ.get("RECOVERYBOOT_STATE_DIR")
    if root:
        return Path(root) / "last_firmware_telemetry.json"
    if os.name == "nt":
        pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(pd) / "RecoveryBoot" / "state" / "last_firmware_telemetry.json"
    return Path.cwd() / ".recoveryboot_state" / "last_firmware_telemetry.json"


def load_previous_snapshot() -> Optional[FirmwareTelemetrySnapshot]:
    path = telemetry_state_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return FirmwareTelemetrySnapshot.from_dict(data)
    except (OSError, ValueError, TypeError):
        append_log("error.log", "failed to parse firmware telemetry snapshot")
        return None


def save_snapshot(snapshot: FirmwareTelemetrySnapshot) -> None:
    path = telemetry_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")
    except OSError:
        append_log("error.log", "failed to persist firmware telemetry snapshot")


def describe_firmware_drift(
    previous: Optional[FirmwareTelemetrySnapshot],
    current: FirmwareTelemetrySnapshot,
) -> Optional[str]:
    """
    Summarize drift vs last persisted snapshot.

    ``previous is None``: first run after install — no drift message.
    """
    if previous is None:
        return None
    if previous.boot_order != current.boot_order:
        return "boot_order_changed"
    if previous.recovery_identifier and current.recovery_identifier is None:
        return "recovery_boot_entry_removed"
    if previous.windows_identifier and current.windows_identifier is None:
        return "windows_boot_manager_removed"
    if previous.boot_next != current.boot_next:
        return "boot_next_changed"
    return None


def poll_cycle_note(previous: Optional[FirmwareTelemetrySnapshot], current: FirmwareTelemetrySnapshot) -> str:
    drift = describe_firmware_drift(previous, current)
    if drift:
        append_log("bootorder.log", f"telemetry drift: {drift}")
        return drift
    return "no_drift"

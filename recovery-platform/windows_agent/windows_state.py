"""Aggregated Windows-side agent state (EFI/NVRAM monitoring only)."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple

from boot_manager.boot_entry import FirmwareAnalysisResult
from boot_manager.bootorder_planner import BootOrderPlan

from windows_agent.preflight import WindowsSystemProbes


@dataclass
class WindowsState:
    """Runtime view for RecoveryBoot monitoring (no Recovery Image access)."""

    bitlocker_state: str = "UNKNOWN"
    secure_boot_state: str = "UNKNOWN"
    bootorder_state: str = "UNKNOWN"
    recoveryboot_present: bool = False
    windows_boot_present: bool = False
    shim_present: bool = False
    repair_required: bool = False
    repair_blocked_reason: Optional[str] = None
    is_windows: bool = False
    is_admin: bool = False
    firmware_status: str = "UNKNOWN"
    plan_status: str = "UNKNOWN"
    extra_notes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _bitlocker_blocks_repair(bitlocker: str) -> bool:
    return bitlocker.strip().upper() == "ON"


def _shim_reported(analysis: FirmwareAnalysisResult) -> bool:
    rb = analysis.recovery_boot
    if rb is None or not rb.path:
        return False
    p = rb.path.replace("\\", "/").lower()
    return "shimx64.efi" in p


def build_windows_state(
    *,
    analysis: FirmwareAnalysisResult,
    plan: BootOrderPlan,
    probes: WindowsSystemProbes,
    bitlocker_state: str,
    secure_boot_state: str,
) -> WindowsState:
    """Derive agent-facing state from firmware analysis and BootOrder plan."""

    is_win = probes.is_windows()
    admin = probes.is_admin() if is_win else False
    windows_ok = analysis.windows_boot_manager is not None
    recovery_ok = analysis.recovery_boot is not None

    if not windows_ok:
        bootorder_state = "FAIL"
    elif plan.status == "PASS" and not plan.action_required:
        bootorder_state = "OK"
    elif plan.action_required and plan.status == "PLANNED":
        bootorder_state = "NEEDS_REPAIR"
    else:
        bootorder_state = "UNKNOWN"

    base_repair = bool(plan.action_required and plan.status == "PLANNED")
    blocked: Optional[str] = None

    if not is_win:
        blocked = "not Windows"
    elif not admin:
        blocked = "administrator privileges required"
    elif not windows_ok:
        blocked = "Windows Boot Manager firmware entry not found"
    elif analysis.status != "PASS":
        blocked = "firmware enumeration failed or incomplete"
    elif plan.status == "FAIL":
        blocked = plan.reason or "BootOrder plan failed"
    elif _bitlocker_blocks_repair(bitlocker_state):
        blocked = "BitLocker enabled. BootOrder repair blocked."

    repair_required = base_repair and blocked is None

    extra: Tuple[str, ...] = ()
    if _bitlocker_blocks_repair(bitlocker_state):
        extra = ("BitLocker enabled. BootOrder repair blocked.",)

    return WindowsState(
        bitlocker_state=bitlocker_state,
        secure_boot_state=secure_boot_state,
        bootorder_state=bootorder_state,
        recoveryboot_present=recovery_ok,
        windows_boot_present=windows_ok,
        shim_present=_shim_reported(analysis),
        repair_required=repair_required,
        repair_blocked_reason=blocked,
        is_windows=is_win,
        is_admin=admin,
        firmware_status=analysis.status,
        plan_status=plan.status,
        extra_notes=extra,
    )


def assert_windows_agent_platform() -> None:
    """Fail fast if a Windows-only entry point runs on Linux (Recovery Runtime host)."""
    if sys.platform != "win32":
        raise RuntimeError("windows_agent modules are Windows-only")

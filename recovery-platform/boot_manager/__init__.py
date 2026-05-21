"""UEFI boot entry management (non-destructive to Windows Boot Manager)."""

from .boot_entry import BootEntry, FirmwareAnalysisResult, analyze_firmware_output
from .bootorder_planner import BootOrderPlan, plan_bootorder_recovery
from .efi_installer import EfiInstallResult, run_efi_installer
from .firmware_reader import read_firmware_boot

__all__ = [
    "BootEntry",
    "BootOrderPlan",
    "EfiInstallResult",
    "FirmwareAnalysisResult",
    "analyze_firmware_output",
    "plan_bootorder_recovery",
    "read_firmware_boot",
    "run_efi_installer",
]

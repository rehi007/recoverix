"""EFI RecoveryBoot file placement with dry-run / apply modes."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from common.command import run_readonly
from common.errors import BitLockerActiveError, RecoveryError
from common.logger import get_logger, setup_logging
from partition_manager.discovery import discover_partitions
from partition_manager.models import EFI_BOOT_RELATIVE, PartitionRecord, PartitionState
from validation.system_check import (
    merge_bitlocker_states,
    parse_bitlocker_volumes_json,
    parse_manage_bde_status,
)

logger = get_logger(__name__)

LOG_FILE = Path(r"C:\ProgramData\Recoverix\logs\efi_installer.log")
BACKUP_ROOT = Path(r"C:\ProgramData\Recoverix\backup\efi")
PROGRAM_DATA_ROOT = Path(r"C:\ProgramData\Recoverix")

RECOVERY_EFI_RELATIVE = (
    r"EFI\RecoveryBoot\shimx64.efi",
    r"EFI\RecoveryBoot\grubx64.efi",
    r"EFI\RecoveryBoot\grub.cfg",
)
WINDOWS_BOOTMGFW_RELATIVE = EFI_BOOT_RELATIVE

_POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


@dataclass(frozen=True)
class EfiFileAction:
    """A single planned or applied file operation."""

    source: str
    destination: str
    backup_destination: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EfiInstallResult:
    """Result of EFI installation planning or apply."""

    dry_run: bool
    apply: bool
    status: str
    esp_mount: Optional[str] = None
    bitlocker: str = "UNKNOWN"
    backup_plan: List[str] = field(default_factory=list)
    planned_actions: List[str] = field(default_factory=list)
    file_actions: List[EfiFileAction] = field(default_factory=list)
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "apply": self.apply,
            "status": self.status,
            "esp_mount": self.esp_mount,
            "bitlocker": self.bitlocker,
            "backup_plan": list(self.backup_plan),
            "planned_actions": list(self.planned_actions),
            "file_actions": [action.to_dict() for action in self.file_actions],
            "reason": self.reason,
        }

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets" / "recovery_boot"


def _normalize_relative(path: str) -> str:
    return path.replace("\\", "/").lower()


def _assert_safe_destination(relative_path: str) -> None:
    """Refuse any operation that would touch Windows Boot Manager."""
    normalized = _normalize_relative(relative_path)
    if _normalize_relative(WINDOWS_BOOTMGFW_RELATIVE) in normalized:
        raise RecoveryError("refusing to modify Windows Boot Manager path")
    if "efi/microsoft/boot/bootmgfw.efi" in normalized:
        raise RecoveryError("refusing to overwrite bootmgfw.efi")
    if not normalized.startswith("efi/recoveryboot/"):
        raise RecoveryError(f"destination outside RecoveryBoot tree: {relative_path}")


def resolve_esp_mount(efi_partition: Optional[PartitionRecord]) -> Optional[str]:
    """Resolve ESP mount path from discovered EFI partition metadata."""
    if efi_partition is None:
        return None
    if efi_partition.state != PartitionState.FOUND.value:
        return None

    if efi_partition.drive_letter:
        return f"{efi_partition.drive_letter}:\\"

    for raw_path in efi_partition.access_paths:
        path = str(raw_path).rstrip("\\")
        if path:
            return f"{path}\\"

    return None


def read_bitlocker_state(*, live: bool) -> str:
    """Read BitLocker state using read-only Windows commands."""
    if not live or sys.platform != "win32":
        return "UNKNOWN"

    manage = run_readonly(["manage-bde", "-status"])
    manage_state = (
        parse_manage_bde_status(manage.stdout) if manage.returncode == 0 else None
    )

    ps = run_readonly(
        _POWERSHELL
        + ["Get-BitLockerVolume | Select-Object VolumeStatus, ProtectionStatus | ConvertTo-Json"]
    )
    ps_state = (
        parse_bitlocker_volumes_json(ps.stdout) if ps.returncode == 0 else None
    )
    return merge_bitlocker_states(manage_state, ps_state)


def discover_esp(*, live: bool) -> tuple[Optional[str], Optional[PartitionRecord], str]:
    """Discover EFI System Partition mount using partition_manager."""
    discovery = discover_partitions(dry_run=not live)
    efi = discovery.efi_partition
    if efi is None or efi.state != PartitionState.FOUND.value:
        reason = "EFI System Partition not found"
        if efi and efi.reason:
            reason = efi.reason
        logger.warning(reason)
        return None, efi, reason

    mount = resolve_esp_mount(efi)
    if mount is None:
        reason = "ESP mount path could not be resolved"
        logger.warning(reason)
        return None, efi, reason

    logger.info("ESP resolved: %s (disk=%s part=%s)", mount, efi.disk_number, efi.partition_number)
    return mount, efi, ""


def _backup_directory() -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return BACKUP_ROOT / stamp


def build_file_actions(esp_mount: str) -> List[EfiFileAction]:
    """Build source/destination pairs for RecoveryBoot files."""
    esp_root = Path(str(esp_mount).rstrip("\\/"))
    assets = _assets_dir()
    actions: List[EfiFileAction] = []

    for relative in RECOVERY_EFI_RELATIVE:
        _assert_safe_destination(relative)
        filename = relative.rsplit("\\", 1)[-1]
        source = assets / filename
        relative_parts = relative.replace("\\", "/").split("/")
        destination = esp_root.joinpath(*relative_parts)
        actions.append(
            EfiFileAction(
                source=str(source),
                destination=str(destination),
            )
        )
    return actions


def build_backup_plan(
    file_actions: Sequence[EfiFileAction],
    *,
    backup_dir: Path,
) -> List[str]:
    """Create backup plan entries for existing destination files."""
    plan: List[str] = []
    for action in file_actions:
        dest = Path(action.destination)
        if not dest.exists():
            plan.append(f"skip backup (not present): {dest}")
            continue
        backup_path = backup_dir / dest.name
        plan.append(f"backup {dest} -> {backup_path}")
    return plan


def _attach_backup_paths(
    actions: List[EfiFileAction],
    *,
    backup_dir: Path,
) -> List[EfiFileAction]:
    updated: List[EfiFileAction] = []
    for action in actions:
        dest = Path(action.destination)
        backup_path = None
        if dest.exists():
            backup_path = str(backup_dir / dest.name)
        updated.append(
            EfiFileAction(
                source=action.source,
                destination=action.destination,
                backup_destination=backup_path,
            )
        )
    return updated


def _run_backups(actions: Sequence[EfiFileAction], *, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    for action in actions:
        if not action.backup_destination:
            continue
        src = Path(action.destination)
        dst = Path(action.backup_destination)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        logger.info("backed up %s -> %s", src, dst)


def _validate_destination_under_esp(dest: Path, esp_root: Path) -> None:
    relative = dest.relative_to(esp_root)
    _assert_safe_destination(str(relative))


def _install_files(actions: Sequence[EfiFileAction], *, esp_mount: str) -> None:
    esp_root = Path(esp_mount)
    for action in actions:
        dest_path = Path(action.destination)
        _validate_destination_under_esp(dest_path, esp_root)
        src = Path(action.source)
        if not src.exists():
            raise RecoveryError(f"placeholder asset missing: {src}")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path)
        logger.info("installed %s -> %s", src, dest_path)


def run_efi_installer(*, dry_run: bool = True, apply: bool = False) -> EfiInstallResult:
    """
    Plan or apply RecoveryBoot EFI file placement.

    Apply mode refuses BitLocker ON and never touches bootmgfw.efi.
    """
    on_windows = sys.platform == "win32"
    live_discovery = on_windows
    bitlocker = read_bitlocker_state(live=on_windows)

    if apply and bitlocker == "ON":
        logger.error("BitLocker is ON; refusing EFI modification")
        raise BitLockerActiveError("BitLocker is ON; EFI modification refused")

    esp_mount, _efi_part, esp_reason = discover_esp(live=live_discovery)
    if esp_mount is None:
        return EfiInstallResult(
            dry_run=dry_run,
            apply=apply,
            status="FAIL",
            esp_mount=None,
            bitlocker=bitlocker,
            reason=esp_reason or "ESP not available",
        )

    bootmgfw_path = Path(esp_mount) / WINDOWS_BOOTMGFW_RELATIVE
    if bootmgfw_path.exists():
        logger.info("Windows bootmgfw.efi present (will not modify): %s", bootmgfw_path)

    file_actions = build_file_actions(esp_mount)
    backup_dir = _backup_directory()
    backup_plan = build_backup_plan(file_actions, backup_dir=backup_dir)
    file_actions = _attach_backup_paths(file_actions, backup_dir=backup_dir)

    planned_actions = [
        "verify ESP mount",
        "verify bootmgfw.efi will not be modified",
        *backup_plan,
        *[f"install {Path(action.source).name} -> {action.destination}" for action in file_actions],
    ]

    if dry_run and not apply:
        logger.info("dry-run: EFI install plan only (%d files)", len(file_actions))
        return EfiInstallResult(
            dry_run=True,
            apply=False,
            status="PLANNED",
            esp_mount=esp_mount,
            bitlocker=bitlocker,
            backup_plan=backup_plan,
            planned_actions=planned_actions,
            file_actions=file_actions,
            reason="Dry-run plan only; no files copied",
        )

    if not apply:
        return EfiInstallResult(
            dry_run=False,
            apply=False,
            status="PLANNED",
            esp_mount=esp_mount,
            bitlocker=bitlocker,
            backup_plan=backup_plan,
            planned_actions=planned_actions,
            file_actions=file_actions,
            reason="Live plan without --apply",
        )

    logger.info("apply mode: executing backup and install")
    _run_backups(file_actions, backup_dir=backup_dir)
    _install_files(file_actions, esp_mount=esp_mount)
    return EfiInstallResult(
        dry_run=False,
        apply=True,
        status="APPLIED",
        esp_mount=esp_mount,
        bitlocker=bitlocker,
        backup_plan=backup_plan,
        planned_actions=planned_actions,
        file_actions=file_actions,
        reason=None,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging(log_file=LOG_FILE if sys.platform == "win32" else None)
    parser = argparse.ArgumentParser(description="RecoveryBoot EFI file installer")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Plan only (default mode)")
    mode.add_argument("--apply", action="store_true", help="Apply file placement after checks")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        result = run_efi_installer(dry_run=args.dry_run, apply=args.apply)
    except BitLockerActiveError as exc:
        logger.error(str(exc))
        result = EfiInstallResult(
            dry_run=False,
            apply=True,
            status="REJECTED",
            bitlocker="ON",
            reason=str(exc),
        )
        if args.json:
            print(result.to_json())
        return 2

    if args.json:
        print(result.to_json())
    else:
        print(result.to_json())

    if result.status in ("APPLIED", "PLANNED", "PASS"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

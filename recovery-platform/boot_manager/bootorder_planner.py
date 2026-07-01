"""Dry-run BootOrder recovery planning (no bcdedit execution)."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from boot_manager.boot_entry import BootEntry, FirmwareAnalysisResult
from boot_manager.firmware_reader import read_firmware_boot
from common.logger import get_logger, setup_logging

logger = get_logger(__name__)

_FWBOOTMGR = "{fwbootmgr}"

@dataclass(frozen=True)
class BootOrderPlan:
    """Planned BootOrder recovery actions (not executed)."""

    action_required: bool
    planned_actions: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    dry_run: bool = True
    status: str = "FAIL"
    create_required: bool = False
    reorder_required: bool = False
    boot_next_policy: str = "preserve"
    target_boot_order: List[str] = field(default_factory=list)
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _entry_identifier(entry: Optional[BootEntry]) -> Optional[str]:
    if entry is None:
        return None
    return entry.identifier


def _current_positions(
    boot_order: List[str],
    *,
    recovery_id: Optional[str],
    windows_id: str,
) -> tuple[Optional[int], Optional[int]]:
    recovery_pos = boot_order.index(recovery_id) if recovery_id in boot_order else None
    windows_pos = boot_order.index(windows_id) if windows_id in boot_order else None
    return recovery_pos, windows_pos


RECOVERIX_BOOT_MANAGER_LABEL = "Recoverix Boot Manager"


def build_target_boot_order(
    *,
    recovery_id: Optional[str],
    windows_id: str,
    current_order: List[str],
) -> List[str]:
    """Place Recoverix Boot Manager first, Windows Boot Manager second, preserve others."""
    remaining = [
        identifier
        for identifier in current_order
        if identifier not in {recovery_id, windows_id}
    ]
    target: List[str] = []
    if recovery_id:
        target.append(recovery_id)
    target.append(windows_id)
    target.extend(remaining)
    return target


def format_displayorder_command(boot_order: Sequence[str]) -> str:
    """Format a planned displayorder command (not executed)."""
    ordered = " ".join(boot_order)
    return f"bcdedit /set {_FWBOOTMGR} displayorder {ordered}"


def plan_bootorder_recovery(
    analysis: FirmwareAnalysisResult,
    *,
    dry_run: bool = True,
) -> BootOrderPlan:
    """
    Build a dry-run plan to prioritize Recoverix Boot Manager first and Windows second.

    Does not execute bcdedit or modify EFI / BootNext.
    """
    windows = analysis.windows_boot_manager
    recovery = analysis.recovery_boot
    windows_id = _entry_identifier(windows)

    if windows is None or windows_id is None:
        logger.error("Windows Boot Manager entry missing; cannot plan recovery")
        return BootOrderPlan(
            action_required=False,
            dry_run=dry_run,
            status="FAIL",
            reason="Windows Boot Manager entry not found",
            boot_next_policy="preserve",
        )

    recovery_id = _entry_identifier(recovery)
    create_required = recovery is None
    recovery_pos, windows_pos = _current_positions(
        analysis.boot_order,
        recovery_id=recovery_id,
        windows_id=windows_id,
    )

    reorder_required = False
    if create_required:
        reorder_required = True
    else:
        if recovery_pos != 0:
            reorder_required = True
        if windows_pos != 1:
            reorder_required = True

    planned_actions: List[str] = []
    commands: List[str] = []

    if create_required:
        logger.error(
            "%s entry missing; bcdedit creation disabled pending native NVRAM writer",
            RECOVERIX_BOOT_MANAGER_LABEL,
        )
        return BootOrderPlan(
            action_required=False,
            planned_actions=[f"{RECOVERIX_BOOT_MANAGER_LABEL} entry missing; native NVRAM writer required"],
            commands=[],
            dry_run=dry_run,
            status="FAIL",
            create_required=True,
            reorder_required=False,
            boot_next_policy="preserve",
            target_boot_order=build_target_boot_order(
                recovery_id=None,
                windows_id=windows_id,
                current_order=analysis.boot_order,
            ),
            reason=(
                f"{RECOVERIX_BOOT_MANAGER_LABEL} creation via bcdedit is disabled; "
                "native UEFI NVRAM writer required"
            ),
        )

    target_order = build_target_boot_order(
        recovery_id=recovery_id,
        windows_id=windows_id,
        current_order=analysis.boot_order,
    )

    if create_required and recovery_id is None:
        target_order = build_target_boot_order(
            recovery_id="{recovery-boot-id}",
            windows_id=windows_id,
            current_order=analysis.boot_order,
        )

    if reorder_required:
        planned_actions.append(f"set {RECOVERIX_BOOT_MANAGER_LABEL} first")
        planned_actions.append("set Windows Boot Manager second")
        commands.append(format_displayorder_command(target_order))
        logger.info("plan: BootOrder reorder required -> %s", target_order)

    action_required = create_required or reorder_required

    if action_required:
        if analysis.boot_next:
            planned_actions.append(f"preserve BootNext ({analysis.boot_next})")
        else:
            planned_actions.append("preserve BootNext")
    status = "PASS" if not action_required else "PLANNED"

    return BootOrderPlan(
        action_required=action_required,
        planned_actions=planned_actions,
        commands=commands,
        dry_run=dry_run,
        status=status,
        create_required=create_required,
        reorder_required=reorder_required,
        boot_next_policy="preserve",
        target_boot_order=target_order,
        reason=None if action_required else "BootOrder already matches target layout",
    )


def build_plan_from_system(*, live: bool, dry_run: bool) -> BootOrderPlan:
    """Load firmware analysis and build a BootOrder recovery plan."""
    analysis = read_firmware_boot(dry_run=not live)
    return plan_bootorder_recovery(analysis, dry_run=dry_run)


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(
        description="Generate a dry-run BootOrder recovery plan",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Plan only; never execute bcdedit changes (default: true)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Read firmware boot data before planning (still no modifications)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = build_plan_from_system(live=args.live, dry_run=True)
    print(plan.to_json())
    return 0 if plan.status in ("PASS", "PLANNED") else 1


if __name__ == "__main__":
    raise SystemExit(main())

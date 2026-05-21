"""BootOrder inspection and optional repair (EFI/NVRAM via bcdedit only)."""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Callable, List, Optional

from boot_manager.bootorder_planner import BootOrderPlan, plan_bootorder_recovery
from boot_manager.firmware_reader import read_firmware_boot
from common.command import run_command
from common.logger import get_logger, setup_logging
from windows_agent.logging_config import append_log, get_log_directory, repair_logger
from windows_agent.preflight import WindowsSystemProbes
from windows_agent.windows_state import build_windows_state

logger = get_logger(__name__)

_GUID_BRACE = re.compile(r"\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}")

RECOVERY_BOOT_ID_PLACEHOLDER = "{recovery-boot-id}"


def _parse_created_entry_id(stdout: str) -> Optional[str]:
    for line in stdout.splitlines():
        m = _GUID_BRACE.search(line)
        if m:
            return m.group(0)
    m = _GUID_BRACE.search(stdout)
    return m.group(0) if m else None


def apply_bootorder_plan(
    plan: BootOrderPlan,
    *,
    apply_changes: bool,
    confirmed: bool,
    command_runner: Callable[..., object] = run_command,
) -> List[str]:
    """
    Execute planned bcdedit commands. When apply_changes is False, no writes occur.
    BitLocker / policy checks must be done by the caller.
    """
    log_lines: List[str] = []
    if not apply_changes or not plan.action_required:
        return log_lines

    created_id: Optional[str] = None
    for raw_cmd in plan.commands:
        cmd = raw_cmd.strip()
        if RECOVERY_BOOT_ID_PLACEHOLDER in cmd and created_id:
            cmd = cmd.replace(RECOVERY_BOOT_ID_PLACEHOLDER, created_id)

        if "/create" in cmd:
            argv = ["cmd.exe", "/c", cmd] if os.name == "nt" else ["sh", "-c", cmd]
            result = command_runner(argv, dry_run=False, confirmed=confirmed)
            stdout = getattr(result, "stdout", "") or ""
            created_id = _parse_created_entry_id(stdout) or created_id
            msg = f"bcdedit create rc={getattr(result, 'returncode', -1)!r}"
            log_lines.append(msg)
            repair_logger().info("create boot entry: %s", msg)
            continue

        argv = ["cmd.exe", "/c", cmd] if os.name == "nt" else ["sh", "-c", cmd]
        result = command_runner(argv, dry_run=False, confirmed=confirmed)
        msg = f"bcdedit cmd rc={getattr(result, 'returncode', -1)!r}"
        log_lines.append(msg)
        repair_logger().info("bcdedit: %s", cmd)

    return log_lines


def run_fix_bootorder_task(
    *,
    dry_run: bool = True,
    apply_changes: bool = False,
    probes: Optional[WindowsSystemProbes] = None,
    read_boot: Callable[..., object] = read_firmware_boot,
    plan_fn: Callable[..., BootOrderPlan] = plan_bootorder_recovery,
    command_runner: Callable[..., object] = run_command,
) -> int:
    """
    Inspect firmware entries and optionally apply BootOrder repair.

    Default: dry-run (read-only firmware enum; never runs bcdedit writes).
    ``--apply`` sets apply_changes=True; still refuses if BitLocker is ON or not admin.
    """
    setup_logging()
    pd = get_log_directory()
    pd.mkdir(parents=True, exist_ok=True)

    if sys.platform != "win32":
        logger.error("fix_bootorder_task requires Windows")
        return 2

    probes = probes or WindowsSystemProbes()
    if not probes.is_admin():
        append_log("error.log", "fix_bootorder_task: insufficient privileges")
        logger.error("administrator privileges required")
        return 3

    bl = probes.bitlocker_state()
    sb = probes.secure_boot_state()

    analysis = read_boot(dry_run=False)  # type: ignore[operator]
    plan = plan_fn(analysis, dry_run=False)  # type: ignore[operator]
    state = build_windows_state(
        analysis=analysis,  # type: ignore[arg-type]
        plan=plan,
        probes=probes,
        bitlocker_state=bl,
        secure_boot_state=sb,
    )

    append_log(
        "bootorder.log",
        f"state bitlocker={bl} secure_boot={sb} bootorder={state.bootorder_state} "
        f"repair_required={state.repair_required} blocked={state.repair_blocked_reason!r}",
    )

    if dry_run or not apply_changes:
        repair_logger().info("dry-run: skipping bcdedit writes")
        append_log("repair.log", "dry-run: no bcdedit writes")
        print(plan.to_json())
        return 0 if plan.status in ("PASS", "PLANNED") else 1

    if state.repair_blocked_reason:
        append_log("repair.log", f"repair blocked: {state.repair_blocked_reason}")
        repair_logger().warning("repair blocked: %s", state.repair_blocked_reason)
        print(plan.to_json())
        return 4

    logs = apply_bootorder_plan(
        plan,
        apply_changes=True,
        confirmed=True,
        command_runner=command_runner,
    )
    for line in logs:
        append_log("repair.log", line)

    print(plan.to_json())
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RecoveryBoot BootOrder inspection / repair")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="run planned bcdedit commands (default: dry-run / read-only plan)",
    )
    parsed = parser.parse_args(argv)

    apply_changes = bool(parsed.apply)
    dry_run = not apply_changes
    return run_fix_bootorder_task(dry_run=dry_run, apply_changes=apply_changes)


if __name__ == "__main__":
    raise SystemExit(main())

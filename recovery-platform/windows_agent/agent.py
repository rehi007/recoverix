"""RecoveryBoot Windows Agent — EFI/NVRAM monitoring and BootOrder repair only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional

from boot_manager.boot_entry import FirmwareAnalysisResult
from boot_manager.bootorder_planner import BootOrderPlan, plan_bootorder_recovery
from boot_manager.firmware_reader import read_firmware_boot
from common.command import run_command
from common.logger import get_logger, setup_logging
from windows_agent.event_monitor import (
    FirmwareTelemetrySnapshot,
    load_previous_snapshot,
    poll_cycle_note,
    save_snapshot,
)
from windows_agent.fix_bootorder_task import apply_bootorder_plan
from windows_agent.logging_config import agent_logger, append_log, ensure_agent_file_logging, log_exception
from windows_agent.nvram_writer import run_native_nvram_writer
from windows_agent.preflight import WindowsSystemProbes
from windows_agent.task_scheduler import register_recovery_boot_monitor_task
from windows_agent.windows_state import build_windows_state

logger = get_logger(__name__)


def run_agent_startup(
    *,
    apply_repairs: bool = True,
    probes: Optional[WindowsSystemProbes] = None,
    read_boot: Callable[..., FirmwareAnalysisResult] = read_firmware_boot,
    plan_boot: Callable[..., BootOrderPlan] = plan_bootorder_recovery,
    command_runner: Callable[..., object] = run_command,
    native_writer: Callable[..., int] = run_native_nvram_writer,
) -> int:
    """
    Agent startup flow: preflight → telemetry → plan → optional repair.

    Does not mount ext4/squashfs or touch Recovery Image contents.
    """
    setup_logging()
    ensure_agent_file_logging()
    agent_logger().info("recovery boot agent startup")

    if sys.platform != "win32":
        logger.error("windows_agent runs on Windows only")
        return 2

    probes = probes or WindowsSystemProbes()
    if not probes.is_admin():
        append_log("error.log", "agent: insufficient privileges (need Administrator/SYSTEM)")
        agent_logger().error("administrator privileges required")
        print("RecoveryBoot agent: administrator privileges required", file=sys.stderr)
        return 3

    bitlocker = probes.bitlocker_state()
    secure_boot = probes.secure_boot_state()

    try:
        analysis = read_boot(dry_run=False)
        prior = load_previous_snapshot()
        snapshot = FirmwareTelemetrySnapshot.from_analysis(analysis)
        drift = poll_cycle_note(prior, snapshot)
        save_snapshot(snapshot)

        append_log(
            "windows_agent.log",
            f"preflight bitlocker={bitlocker} secure_boot={secure_boot} drift={drift!r}",
        )

        plan = plan_boot(analysis, dry_run=False)
        state = build_windows_state(
            analysis=analysis,
            plan=plan,
            probes=probes,
            bitlocker_state=bitlocker,
            secure_boot_state=secure_boot,
        )

        append_log(
            "bootorder.log",
            f"agent state repair_required={state.repair_required} "
            f"blocked={state.repair_blocked_reason!r} plan={plan.status}",
        )

        for note in state.extra_notes:
            agent_logger().warning(note)

        if bitlocker.strip().upper() == "ON":
            agent_logger().warning("BitLocker enabled. BootOrder repair blocked.")

        if not apply_repairs:
            agent_logger().info("apply_repairs disabled; skipping writes")
            return 0

        if state.repair_blocked_reason:
            agent_logger().warning("repair blocked: %s", state.repair_blocked_reason)

        if (
            apply_repairs
            and plan.create_required
            and plan.status == "FAIL"
            and "native UEFI NVRAM writer required" in (plan.reason or "")
            and bitlocker.strip().upper() != "ON"
            and analysis.windows_boot_manager is not None
        ):
            rc = native_writer(working_directory=Path.cwd(), command_runner=command_runner)
            if rc == 0:
                append_log("repair.log", "native writer applied RecoveryBoot repair")
                agent_logger().info("native writer applied RecoveryBoot repair")
                return 0
            append_log("error.log", f"native writer failed rc={rc}")
            return 5

        if not state.repair_required:
            if plan.action_required and plan.status == "PLANNED":
                agent_logger().info("BootOrder repair deferred (blocked or skipped)")
            else:
                agent_logger().info("no BootOrder repair needed")
            return 0

        apply_bootorder_plan(plan, apply_changes=True, confirmed=True, command_runner=command_runner)
        agent_logger().info("BootOrder repair commands executed")
        append_log("repair.log", "agent applied BootOrder repair plan")
        return 0

    except Exception as exc:  # noqa: BLE001 — policy: never crash the agent host
        log_exception("agent startup failed", exc)
        print(f"RecoveryBoot agent error: {exc}", file=sys.stderr)
        return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="RecoveryBoot Windows agent (EFI/NVRAM only)")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run a single monitoring cycle (also the default behavior)",
    )
    parser.add_argument(
        "--no-repair",
        action="store_true",
        help="inspect and log only; never run bcdedit writes",
    )
    parser.add_argument(
        "--install-task",
        action="store_true",
        help="register RecoveryBootMonitor scheduled task and exit",
    )
    parser.add_argument(
        "--install-task-dry-run",
        action="store_true",
        help="with --install-task: log registration only",
    )
    parser.add_argument(
        "--python-for-task",
        type=Path,
        default=None,
        help="Python executable for scheduled task (default: sys.executable)",
    )
    parser.add_argument(
        "--task-working-directory",
        type=Path,
        default=None,
        help="Working directory for scheduled task",
    )
    args = parser.parse_args(argv)

    if args.install_task:
        py = args.python_for_task or Path(sys.executable)
        return register_recovery_boot_monitor_task(
            python_executable=py,
            working_directory=args.task_working_directory,
            dry_run=args.install_task_dry_run,
        )

    # Invoked without subcommand: single cycle (Scheduled Task uses: -m windows_agent.agent --once).
    apply_repairs = not args.no_repair
    _ = args.once  # accepted for symmetry with documented task CLI
    return run_agent_startup(apply_repairs=apply_repairs)


if __name__ == "__main__":
    raise SystemExit(main())

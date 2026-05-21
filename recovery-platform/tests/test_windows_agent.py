"""Tests for Windows RecoveryBoot agent (mocked; runnable on Linux CI)."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from boot_manager.boot_entry import BootEntry, FirmwareAnalysisResult
from boot_manager.bootorder_planner import BootOrderPlan, plan_bootorder_recovery
from windows_agent import fix_bootorder_task as fix_mod
from windows_agent import windows_state as ws_mod
from windows_agent.event_monitor import FirmwareTelemetrySnapshot, describe_firmware_drift
from windows_agent.preflight import WindowsSystemProbes


@contextmanager
def _agent_env():
    with tempfile.TemporaryDirectory() as tmp:
        logs = os.path.join(tmp, "logs")
        state = os.path.join(tmp, "state")
        with patch.dict(
            os.environ,
            {"RECOVERYBOOT_LOG_DIR": logs, "RECOVERYBOOT_STATE_DIR": state},
            clear=False,
        ):
            yield


def _windows_probe(admin: bool = True) -> MagicMock:
    p = MagicMock(spec=WindowsSystemProbes)
    p.is_windows.return_value = True
    p.is_admin.return_value = admin
    p.bitlocker_state.return_value = "OFF"
    p.secure_boot_state.return_value = "UNKNOWN"
    return p


def _analysis(*, recovery: bool = True, windows: bool = True, order_ok: bool = True) -> FirmwareAnalysisResult:
    win_id = "{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}"
    rec_id = "{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}"
    win_entry = (
        BootEntry(
            identifier=win_id,
            description="Windows Boot Manager",
            path=r"\EFI\Microsoft\Boot\bootmgfw.efi",
        )
        if windows
        else None
    )
    rec_entry = (
        BootEntry(
            identifier=rec_id,
            description="RecoveryBoot",
            path=r"\EFI\RecoveryBoot\shimx64.efi",
        )
        if recovery
        else None
    )
    entries = [e for e in (win_entry, rec_entry) if e is not None]

    if windows and recovery and order_ok:
        boot_order = [rec_id, win_id]
    elif windows and recovery:
        boot_order = [win_id, rec_id]
    elif windows:
        boot_order = [win_id]
    else:
        boot_order = []

    status = "PASS" if windows else "FAIL"
    return FirmwareAnalysisResult(
        windows_boot_manager=win_entry,
        recovery_boot=rec_entry,
        boot_order=boot_order,
        boot_next=None,
        entries=entries,
        status=status,
        dry_run=False,
    )


def test_recoveryboot_missing_repair_required():
    analysis = _analysis(recovery=False, windows=True, order_ok=True)
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    state = ws_mod.build_windows_state(
        analysis=analysis,
        plan=plan,
        probes=_windows_probe(True),
        bitlocker_state="OFF",
        secure_boot_state="ON",
    )
    assert state.repair_required is True
    assert plan.action_required is True


def test_windows_boot_manager_missing_fail():
    analysis = _analysis(recovery=True, windows=False, order_ok=False)
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    state = ws_mod.build_windows_state(
        analysis=analysis,
        plan=plan,
        probes=_windows_probe(True),
        bitlocker_state="OFF",
        secure_boot_state="ON",
    )
    assert state.windows_boot_present is False
    assert plan.status == "FAIL"
    assert state.repair_required is False
    assert state.repair_blocked_reason is not None


def test_bitlocker_on_blocks_repair():
    analysis = _analysis(recovery=False, windows=True)
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    state = ws_mod.build_windows_state(
        analysis=analysis,
        plan=plan,
        probes=_windows_probe(True),
        bitlocker_state="ON",
        secure_boot_state="ON",
    )
    assert state.repair_blocked_reason is not None
    assert "BitLocker" in (state.repair_blocked_reason or "")
    assert state.repair_required is False


def test_dry_run_no_bcdedit_writes():
    analysis = _analysis(recovery=False, windows=True)
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    calls: list = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return MagicMock(returncode=0, stdout="", stderr="")

    fix_mod.apply_bootorder_plan(plan, apply_changes=False, confirmed=True, command_runner=fake_run)
    assert calls == []


def test_apply_generates_repair_commands():
    analysis = _analysis(recovery=False, windows=True)
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    assert plan.commands, "expected bcdedit plan commands"


def test_bootorder_ok_repair_unnecessary():
    analysis = _analysis(recovery=True, windows=True, order_ok=True)
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    state = ws_mod.build_windows_state(
        analysis=analysis,
        plan=plan,
        probes=_windows_probe(True),
        bitlocker_state="OFF",
        secure_boot_state="ON",
    )
    assert plan.status == "PASS"
    assert state.repair_required is False


def test_insufficient_privileges_blocks_repair():
    analysis = _analysis(recovery=False, windows=True)
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    state = ws_mod.build_windows_state(
        analysis=analysis,
        plan=plan,
        probes=_windows_probe(admin=False),
        bitlocker_state="OFF",
        secure_boot_state="UNKNOWN",
    )
    assert state.repair_blocked_reason is not None
    assert state.repair_required is False


def test_firmware_parse_fail_closed():
    analysis = FirmwareAnalysisResult(
        windows_boot_manager=None,
        recovery_boot=None,
        boot_order=[],
        boot_next=None,
        entries=[],
        status="FAIL",
        dry_run=False,
    )
    plan = plan_bootorder_recovery(analysis, dry_run=False)
    state = ws_mod.build_windows_state(
        analysis=analysis,
        plan=plan,
        probes=_windows_probe(True),
        bitlocker_state="OFF",
        secure_boot_state="UNKNOWN",
    )
    assert plan.status == "FAIL"
    assert state.repair_required is False
    assert state.repair_blocked_reason is not None


def test_firmware_drift_detects_boot_order_change():
    a = FirmwareTelemetrySnapshot(("{a}",), "{r}", "{w}", None)
    b = FirmwareTelemetrySnapshot(("{b}",), "{r}", "{w}", None)
    assert describe_firmware_drift(a, b) == "boot_order_changed"


def test_fix_bootorder_task_apply_invokes_runner():
    calls: list = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return MagicMock(returncode=0, stdout="{cccccccc-cccc-cccc-cccc-cccccccccccc}", stderr="")

    # Force create path to exercise substitution
    plan2 = BootOrderPlan(
        action_required=True,
        planned_actions=["reorder"],
        commands=[f"bcdedit /set {{fwbootmgr}} displayorder {{recovery-boot-id}} {{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}}"],
        dry_run=False,
        status="PLANNED",
        create_required=False,
        reorder_required=True,
        target_boot_order=["{recovery-boot-id}", "{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}"],
    )
    fix_mod.apply_bootorder_plan(plan2, apply_changes=True, confirmed=True, command_runner=runner)
    assert calls, "bcdedit wrapper should run"


def test_run_fix_bootorder_task_respects_platform():
    with _agent_env():
        with patch.object(fix_mod.sys, "platform", "linux"):
            rc = fix_mod.run_fix_bootorder_task(dry_run=True, apply_changes=False)
    assert rc == 2


def test_run_fix_bootorder_non_admin():
    probes = _windows_probe(admin=False)
    with _agent_env():
        with patch.object(fix_mod.sys, "platform", "win32"):
            rc = fix_mod.run_fix_bootorder_task(
                dry_run=True,
                apply_changes=False,
                probes=probes,
            )
    assert rc == 3


if __name__ == "__main__":
    import sys
    import unittest

    suite = unittest.TestSuite()
    mod = sys.modules[__name__]
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            suite.addTest(unittest.FunctionTestCase(getattr(mod, name)))
    runner = unittest.TextTestRunner(verbosity=2)
    raise SystemExit(not runner.run(suite).wasSuccessful())

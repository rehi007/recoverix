"""Tests for boot_manager.bootorder_planner."""

import json

from boot_manager.boot_entry import BootEntry, FirmwareAnalysisResult
from boot_manager.bootorder_planner import (
    build_target_boot_order,
    format_displayorder_command,
    plan_bootorder_recovery,
)


def _entry(identifier: str, description: str, path: str) -> BootEntry:
    return BootEntry(identifier=identifier, description=description, device="partition=C:", path=path)


def _analysis(
    *,
    windows,
    recovery,
    boot_order,
    boot_next=None,
):
    return FirmwareAnalysisResult(
        windows_boot_manager=windows,
        recovery_boot=recovery,
        boot_order=boot_order,
        boot_next=boot_next,
        entries=[],
        status="PASS",
        dry_run=False,
    )


def test_plan_already_correct_order():
    windows = _entry(
        "{win-id}",
        "Windows Boot Manager",
        r"\EFI\Microsoft\Boot\bootmgfw.efi",
    )
    recovery = _entry(
        "{rec-id}",
        "Recoverix Boot Manager",
        r"\EFI\RecoveryBoot\shimx64.efi",
    )
    analysis = _analysis(
        windows=windows,
        recovery=recovery,
        boot_order=["{rec-id}", "{win-id}", "{other-id}"],
        boot_next="{win-id}",
    )

    plan = plan_bootorder_recovery(analysis, dry_run=True)
    assert plan.action_required is False
    assert plan.status == "PASS"
    assert plan.commands == []


def test_plan_reorder_required():
    windows = _entry(
        "{win-id}",
        "Windows Boot Manager",
        r"\EFI\Microsoft\Boot\bootmgfw.efi",
    )
    recovery = _entry(
        "{rec-id}",
        "Recoverix Boot Manager",
        r"\EFI\RecoveryBoot\shimx64.efi",
    )
    analysis = _analysis(
        windows=windows,
        recovery=recovery,
        boot_order=["{win-id}", "{rec-id}"],
    )

    plan = plan_bootorder_recovery(analysis, dry_run=True)
    assert plan.action_required is True
    assert plan.reorder_required is True
    assert plan.create_required is False
    assert "set Recoverix Hotkey Boot (복구 핫키 대기용) first" in plan.planned_actions
    assert any("displayorder" in cmd for cmd in plan.commands)
    assert plan.target_boot_order[0] == "{rec-id}"
    assert plan.target_boot_order[1] == "{win-id}"


def test_plan_create_recovery_required():
    windows = _entry(
        "{win-id}",
        "Windows Boot Manager",
        r"\EFI\Microsoft\Boot\bootmgfw.efi",
    )
    analysis = _analysis(
        windows=windows,
        recovery=None,
        boot_order=["{win-id}", "{other-id}"],
    )

    plan = plan_bootorder_recovery(analysis, dry_run=True)
    assert plan.action_required is False
    assert plan.create_required is True
    assert plan.reorder_required is False
    assert plan.status == "FAIL"
    assert "Recoverix Hotkey Boot (복구 핫키 대기용) entry missing; native NVRAM writer required" in plan.planned_actions
    assert plan.commands == []
    assert "native UEFI NVRAM writer required" in (plan.reason or "")
    assert plan.target_boot_order[0] == "{win-id}"


def test_plan_missing_windows_fails():
    analysis = _analysis(
        windows=None,
        recovery=_entry(
            "{rec-id}",
            "Recoverix Boot Manager",
            r"\EFI\RecoveryBoot\shimx64.efi",
        ),
        boot_order=["{rec-id}"],
    )

    plan = plan_bootorder_recovery(analysis, dry_run=True)
    assert plan.status == "FAIL"
    assert plan.action_required is False
    assert plan.commands == []


def test_preserve_boot_next_in_actions():
    windows = _entry(
        "{win-id}",
        "Windows Boot Manager",
        r"\EFI\Microsoft\Boot\bootmgfw.efi",
    )
    recovery = _entry(
        "{rec-id}",
        "Recoverix Boot Manager",
        r"\EFI\RecoveryBoot\shimx64.efi",
    )
    analysis = _analysis(
        windows=windows,
        recovery=recovery,
        boot_order=["{win-id}", "{rec-id}"],
        boot_next="{rec-id}",
    )

    plan = plan_bootorder_recovery(analysis, dry_run=True)
    assert plan.boot_next_policy == "preserve"
    assert any("preserve BootNext" in action for action in plan.planned_actions)
    assert not any("bootnext" in cmd.lower() for cmd in plan.commands)


def test_build_target_boot_order_preserves_tail():
    order = build_target_boot_order(
        recovery_id="{rec-id}",
        windows_id="{win-id}",
        current_order=["{win-id}", "{other-a}", "{rec-id}", "{other-b}"],
    )
    assert order == ["{rec-id}", "{win-id}", "{other-a}", "{other-b}"]


def test_format_displayorder_command():
    cmd = format_displayorder_command(["{rec-id}", "{win-id}"])
    assert cmd.startswith("bcdedit /set {fwbootmgr} displayorder")
    assert "{rec-id}" in cmd
    assert "Boot0000" not in cmd


def test_json_output_shape():
    windows = _entry(
        "{win-id}",
        "Windows Boot Manager",
        r"\EFI\Microsoft\Boot\bootmgfw.efi",
    )
    analysis = _analysis(windows=windows, recovery=None, boot_order=["{win-id}"])
    plan = plan_bootorder_recovery(analysis, dry_run=True)
    payload = json.loads(plan.to_json())
    assert payload["dry_run"] is True
    assert "planned_actions" in payload
    assert "commands" in payload

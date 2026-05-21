"""Tests for partition_manager.provisioning_planner."""

import json

from partition_manager.models import DiscoveryResult, PartitionRecord, PartitionState
from partition_manager.provisioning_planner import (
    WindowsDiskUsage,
    build_provisioning_plan,
    bytes_to_gb,
    calculate_sizes,
    create_provisioning_plan,
)


def _windows_record(disk: int = 0, part: int = 3) -> PartitionRecord:
    return PartitionRecord(
        disk_number=disk,
        partition_number=part,
        role="windows",
        state=PartitionState.FOUND.value,
        file_system="NTFS",
        drive_letter="C",
        size_bytes=500 * 1024**3,
    )


def _discovery(windows=None, recovery_image=None, recovery_linux=None) -> DiscoveryResult:
    return DiscoveryResult(
        efi_partition=None,
        msr_partition=None,
        windows_partition=windows,
        recovery_image_partition=recovery_image,
        recovery_linux_partition=recovery_linux,
        status="PASS",
        dry_run=False,
    )


def _usage(
    *,
    used_gb: int = 80,
    free_gb: int = 100,
    unallocated_gb: int = 50,
) -> WindowsDiskUsage:
    gib = 1024**3
    volume_size = (used_gb + free_gb) * gib
    used_bytes = used_gb * gib
    free_bytes = free_gb * gib
    disk_size = volume_size + unallocated_gb * gib
    return WindowsDiskUsage(
        disk_number=0,
        partition_number=3,
        drive_letter="C",
        volume_size_bytes=volume_size,
        volume_used_bytes=used_bytes,
        volume_free_bytes=free_bytes,
        disk_size_bytes=disk_size,
        unallocated_bytes=unallocated_gb * gib,
    )


def test_calculate_sizes_example():
    sizes = calculate_sizes(_usage(used_gb=80, free_gb=100, unallocated_gb=50))
    assert sizes["windows_used_gb"] == 80
    assert sizes["estimated_backup_gb"] == 56
    assert sizes["recommended_recovery_image_gb"] == 70
    assert sizes["recommended_recovery_linux_gb"] == 8


def test_bitlocker_on_fails():
    plan = build_provisioning_plan(
        usage=_usage(),
        discovery=_discovery(windows=_windows_record()),
        bitlocker="ON",
    )
    assert plan.status == "FAIL"
    assert plan.can_provision is False
    assert "BitLocker" in (plan.reason or "")


def test_multiple_windows_unsupported_fails():
    windows = PartitionRecord(
        disk_number=0,
        partition_number=3,
        role="windows",
        state=PartitionState.UNSUPPORTED.value,
        reason="Multiple Windows OS candidates detected (2)",
    )
    plan = build_provisioning_plan(
        usage=_usage(),
        discovery=_discovery(windows=windows),
        bitlocker="OFF",
    )
    assert plan.status == "FAIL"
    assert plan.can_provision is False


def test_recovery_partitions_already_exist_fails():
    recovery = PartitionRecord(
        disk_number=0,
        partition_number=5,
        role="recovery_image",
        state=PartitionState.FOUND.value,
        label="RECOVERY_IMAGE",
    )
    plan = build_provisioning_plan(
        usage=_usage(),
        discovery=_discovery(windows=_windows_record(), recovery_image=recovery),
        bitlocker="OFF",
    )
    assert plan.status == "FAIL"
    assert "already exist" in (plan.reason or "")


def test_can_provision_when_space_sufficient():
    plan = build_provisioning_plan(
        usage=_usage(used_gb=80, free_gb=100, unallocated_gb=50),
        discovery=_discovery(windows=_windows_record()),
        bitlocker="OFF",
    )
    assert plan.can_provision is True
    assert plan.status == "PASS"
    assert any("user confirmation" in action for action in plan.planned_actions)
    assert not any("executed" in action.lower() and "shrink" in action.lower() for action in plan.planned_actions if "not executed" not in action)


def test_insufficient_shrinkable_space_fails():
    plan = build_provisioning_plan(
        usage=_usage(used_gb=200, free_gb=5, unallocated_gb=0),
        discovery=_discovery(windows=_windows_record()),
        bitlocker="OFF",
    )
    assert plan.can_provision is False
    assert plan.status == "FAIL"
    assert "insufficient shrinkable space" in (plan.reason or "")


def test_no_automatic_shrink_in_planned_actions():
    plan = build_provisioning_plan(
        usage=_usage(),
        discovery=_discovery(windows=_windows_record()),
        bitlocker="OFF",
    )
    assert any("await explicit user confirmation" in a for a in plan.planned_actions)
    assert not any(
        "automatic shrink" in action.lower() and "no automatic" not in action.lower()
        for action in plan.planned_actions
    )


def test_json_output_shape():
    plan = build_provisioning_plan(
        usage=_usage(),
        discovery=_discovery(windows=_windows_record()),
        bitlocker="OFF",
    )
    payload = json.loads(plan.to_json())
    assert payload["dry_run"] is True
    assert "shrinkable_gb" in payload
    assert payload["estimated_backup_gb"] == 56


def test_create_provisioning_plan_dry_run_linux():
    plan = create_provisioning_plan(live=False, dry_run=True)
    assert plan.dry_run is True
    assert plan.status == "FAIL"


def test_bytes_to_gb_rounds_up():
    assert bytes_to_gb(1024**3) == 1
    assert bytes_to_gb(1) == 1

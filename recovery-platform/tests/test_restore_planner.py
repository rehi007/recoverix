"""Tests for restore_engine.restore_planner (dry-run only)."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import restore_engine.restore_planner as restore_planner_mod
from backup_engine.backup_planner import _DiscoveredLayout
from backup_engine.hash import sha256_file
from backup_engine.manifest import DiskMetadata, finalize_backup_manifest, ManifestContext
from recovery_runtime.discover import DiscoveredVolume
from restore_engine.restore_planner import (
    RestoreCheckResult,
    build_planned_commands,
    build_restore_plan,
    main,
    verify_recovery_image_accessible,
    verify_target_disk,
    verify_windows_boot_manager,
)


def _layout(*, mount: str | None = "/mnt/recovery") -> _DiscoveredLayout:
    return _DiscoveredLayout(
        windows=DiscoveredVolume(
            name="nvme0n1p3",
            path="/dev/nvme0n1p3",
            label=None,
            fstype="ntfs",
            size=80 * 1024**3,
            mountpoint=None,
            device_type="part",
        ),
        efi=DiscoveredVolume(
            name="nvme0n1p1",
            path="/dev/nvme0n1p1",
            label=None,
            fstype="vfat",
            size=512 * 1024**2,
            mountpoint=None,
            device_type="part",
        ),
        recovery_image=DiscoveredVolume(
            name="nvme0n1p5",
            path="/dev/nvme0n1p5",
            label="RECOVERY_IMAGE",
            fstype="ext4",
            size=200 * 1024**3,
            mountpoint=mount,
            device_type="part",
        ),
        disk_path="/dev/nvme0n1",
        volumes=[],
    )


def _disk() -> DiskMetadata:
    return DiskMetadata(
        disk_guid="{11111111-1111-1111-1111-111111111111}",
        disk_model="TestDisk",
        disk_serial="SN123",
        disk_size=1_000_000_000_000,
        windows_partition_uuid="{22222222-2222-2222-2222-222222222222}",
        efi_partition_uuid="{33333333-3333-3333-3333-333333333333}",
    )


def _populate_backup(root: Path) -> None:
    files = {
        "metadata/gpt_backup.bin": b"gpt-data",
        "images/efi.pcl": b"efi-image",
        "images/system.pcl": b"windows-image",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    ctx = ManifestContext(recovery_root=root, disk=_disk())
    finalize_backup_manifest(ctx)


def test_apply_forbidden():
    assert main(["--apply"]) == 2


def test_dry_run_required():
    try:
        main([])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("expected SystemExit")


def test_mount_required_disables_restore():
    with patch.object(restore_planner_mod, "discover_layout", return_value=(None, _layout(mount=None))):
        plan = build_restore_plan()
    assert plan.restore_disabled is True
    assert plan.restore_allowed is False
    assert plan.execution_allowed is False
    assert plan.simulation_only is True
    assert plan.status == "MOUNT_REQUIRED"


def test_validation_failure_restore_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(mount=str(root))
        wrong_disk = DiskMetadata(
            disk_guid="{other-disk}",
            disk_model="X",
            disk_serial="Y",
            disk_size=1,
            windows_partition_uuid="{wrong-windows}",
            efi_partition_uuid="{wrong-efi}",
        )
        with patch.object(restore_planner_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(restore_planner_mod, "build_disk_metadata", return_value=wrong_disk):
                with patch.object(Path, "exists", return_value=True):
                    plan = build_restore_plan()
    assert plan.restore_disabled is True
    assert plan.restore_allowed is False


def test_restore_allowed_planned_output():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(mount=str(root))
        with patch.object(restore_planner_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(restore_planner_mod, "build_disk_metadata", return_value=_disk()):
                with patch.object(Path, "exists", return_value=True):
                    with patch.object(
                        restore_planner_mod,
                        "verify_windows_boot_manager",
                        return_value=RestoreCheckResult(
                            name="windows_boot_manager",
                            passed=True,
                            status="PLANNED",
                        ),
                    ):
                        plan = build_restore_plan()
    assert plan.simulation_only is True
    assert plan.execution_allowed is False
    assert plan.restore_allowed is True
    assert plan.restore_disabled is False
    assert "windows_partclone_restore" in plan.planned_commands
    assert plan.planned_gpt_rollback.get("simulation_only") is True
    assert plan.planned_bootorder_repair


def test_validation_exception_fail_closed():
    layout = _layout(mount="/mnt/recovery")
    with patch.object(restore_planner_mod, "discover_layout", return_value=(None, layout)):
        with patch.object(
            restore_planner_mod,
            "verify_recovery_image_accessible",
            return_value=RestoreCheckResult(
                name="recovery_image_mount",
                passed=True,
                status="PASS",
            ),
        ):
            with patch.object(restore_planner_mod, "validate_restore", side_effect=RuntimeError("boom")):
                with patch.object(restore_planner_mod, "build_disk_metadata", return_value=_disk()):
                    with patch.object(Path, "exists", return_value=True):
                        plan = build_restore_plan()
    assert plan.restore_disabled is True
    assert "validation_exception" in plan.failure_reasons


def test_dry_run_no_filesystem_writes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(mount=str(root))
        with patch.object(restore_planner_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(restore_planner_mod, "build_disk_metadata", return_value=_disk()):
                with patch.object(Path, "exists", return_value=True):
                    with patch("pathlib.Path.mkdir", side_effect=AssertionError("mkdir")):
                        with patch(
                            "pathlib.Path.write_text",
                            side_effect=AssertionError("write_text"),
                        ):
                            with patch(
                                "common.command.run_command",
                                side_effect=AssertionError("run_command"),
                            ):
                                with patch.object(
                                    restore_planner_mod,
                                    "verify_windows_boot_manager",
                                    return_value=RestoreCheckResult(
                                        name="windows_boot_manager",
                                        passed=True,
                                        status="PLANNED",
                                    ),
                                ):
                                    plan = build_restore_plan()
    assert plan.restore_allowed is True
    assert plan.execution_allowed is False


def test_planned_commands_format():
    root = Path("/mnt/recovery")
    layout = _layout(mount=str(root))
    commands = build_planned_commands(layout=layout, recovery_root=root)
    assert "partclone.ntfs -r" in commands["windows_partclone_restore"]
    assert "partclone.fat -r" in commands["efi_partclone_restore"]
    assert "sgdisk --load-backup" in commands["gpt_restore"]


def test_target_disk_mismatch():
    manifest = {
        "disk_guid": "{other}",
        "windows_partition_uuid": "{w}",
        "efi_partition_uuid": "{e}",
    }
    result = verify_target_disk(_layout(), manifest, _disk())
    assert result.passed is False


def test_recovery_access_without_mount():
    result = verify_recovery_image_accessible(_layout(mount=None))
    assert result.passed is False
    assert result.status == "MOUNT_REQUIRED"


def test_bootmgr_planned_when_esp_unmounted():
    with patch.object(restore_planner_mod, "_find_efi_mount", return_value=None):
        result = verify_windows_boot_manager(_layout())
    assert result.status == "PLANNED"
    assert result.passed is True

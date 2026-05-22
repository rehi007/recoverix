"""Tests for backup_engine.run_backup dry-run and write protection."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from backup_engine.backup_planner import _DiscoveredLayout
import backup_engine.run_backup as run_backup_mod
from backup_engine.run_backup import (
    BackupRunResult,
    build_expected_manifest,
    estimate_required_bytes,
    main,
    plan_backup_run,
    run_backup,
)
from backup_engine.space_estimation import BackupSizeEstimate
from backup_engine.write_guard import WriteGuard, WriteForbiddenError
from recovery_runtime.discover import DiscoveredVolume


def _mock_estimate_ok() -> BackupSizeEstimate:
    return BackupSizeEstimate(
        estimated_used_bytes=49 * 1024**3,
        estimated_required_bytes=50 * 1024**3,
        estimated_required_gb=50.0,
        estimation_method="ntfs_used_space",
        recovery_image_free_bytes=500 * 1024**3,
        can_backup=True,
    )


def _layout(mount: str | None = "/mnt/recovery") -> _DiscoveredLayout:
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


@patch.object(run_backup_mod, "estimate_backup_space")
@patch.object(run_backup_mod, "read_bitlocker_state", return_value="OFF")
@patch.object(run_backup_mod, "discover_layout")
@patch.object(run_backup_mod, "build_disk_metadata")
def test_dry_run_planned_output(mock_disk, mock_discover, _mock_bl, mock_estimate):
    mock_discover.return_value = (None, _layout())
    mock_disk.return_value = MagicMock(
        disk_guid="{disk}",
        disk_model="M",
        disk_serial="S",
        disk_size=1,
        windows_partition_uuid="{w}",
        efi_partition_uuid="{e}",
    )
    mock_estimate.return_value = _mock_estimate_ok()

    result = plan_backup_run(WriteGuard(apply=False, confirmed=False))
    assert result.status == "PLANNED"
    assert result.dry_run is True
    assert result.can_backup is True
    assert "windows_partclone" in result.planned_commands
    assert "efi_backup" in result.planned_commands
    assert "gpt_backup" in result.planned_commands
    assert result.expected_manifest["sha256_hashes"]
    assert result.estimated_required_bytes > 0
    assert result.estimation_method == "ntfs_used_space"
    assert result.estimated_used_bytes == 49 * 1024**3


@patch.object(run_backup_mod, "read_bitlocker_state", return_value="ON")
@patch.object(run_backup_mod, "discover_layout")
def test_bitlocker_rejected(_mock_discover, _mock_bl):
    result = plan_backup_run(WriteGuard(apply=False, confirmed=False))
    assert result.status == "REJECTED"


def test_apply_requires_confirm():
    code = main(["--apply"])
    assert code == 2


def test_dry_run_cli_success():
    with patch.object(run_backup_mod, "run_backup") as mock_run:
        mock_run.return_value = BackupRunResult(
            status="PLANNED",
            dry_run=True,
            apply=False,
            confirmed=False,
            can_backup=True,
        )
        code = main(["--dry-run", "--json"])
    assert code == 0


@patch.object(run_backup_mod, "estimate_backup_space", return_value=_mock_estimate_ok())
@patch.object(run_backup_mod, "run_command")
@patch.object(run_backup_mod, "discover_layout")
@patch.object(run_backup_mod, "read_bitlocker_state", return_value="OFF")
@patch.object(run_backup_mod, "build_disk_metadata")
def test_dry_run_no_command_execution(mock_disk, _mock_bl, mock_discover, mock_cmd, _mock_est):
    mock_discover.return_value = (None, _layout())
    mock_disk.return_value = MagicMock(
        disk_guid="{disk}",
        disk_model="M",
        disk_serial="S",
        disk_size=1,
        windows_partition_uuid="{w}",
        efi_partition_uuid="{e}",
    )
    result = run_backup(apply=False, confirmed=False)
    assert result.dry_run is True
    mock_cmd.assert_not_called()


def test_write_guard_dry_run_mkdir_is_planned_only():
    guard = WriteGuard(apply=False, confirmed=False)
    target = Path(tempfile.mkdtemp()) / "nested" / "dir"
    guard.mkdir(target, operation="test-mkdir")
    assert not target.exists()


def test_write_guard_apply_requires_confirm():
    guard = WriteGuard(apply=True, confirmed=False)
    try:
        guard.write_text(Path("/tmp/x"), "data", operation="test-write")
    except Exception as exc:
        assert "confirm" in str(exc).lower()
    else:
        raise AssertionError("expected confirmation error")


@patch.object(WriteGuard, "mkdir")
@patch.object(run_backup_mod, "estimate_backup_space", return_value=_mock_estimate_ok())
@patch.object(run_backup_mod, "run_command")
@patch.object(run_backup_mod, "finalize_backup_manifest")
@patch.object(run_backup_mod, "_resolve_recovery_mount")
@patch.object(run_backup_mod, "discover_layout")
@patch.object(run_backup_mod, "read_bitlocker_state", return_value="OFF")
@patch.object(run_backup_mod, "build_disk_metadata")
@patch("backup_engine.backup_planner.has_valid_backup", return_value=False)
def test_apply_executes_with_confirm(
    _mock_valid,
    mock_disk,
    _mock_bl,
    mock_discover,
    mock_mount,
    mock_finalize,
    mock_cmd,
    _mock_mkdir,
    _mock_est,
):
    mock_discover.return_value = (None, _layout("/mnt/recovery"))
    mock_mount.return_value = (Path("/mnt/recovery"), ["use existing mount"])
    mock_disk.return_value = MagicMock(
        disk_guid="{disk}",
        disk_model="M",
        disk_serial="S",
        disk_size=1,
        windows_partition_uuid="{w}",
        efi_partition_uuid="{e}",
    )
    mock_cmd.return_value = MagicMock(returncode=0, stdout="", stderr="")
    mock_finalize.return_value = ({}, "hash", Path("/mnt/recovery/recovery-manifest.json"))

    result = run_backup(apply=True, confirmed=True)
    assert result.status == "COMPLETED"
    assert mock_cmd.call_count == 3
    mock_finalize.assert_called_once()


@patch("backup_engine.space_estimation.estimate_ntfs_used_bytes")
@patch("backup_engine.space_estimation.estimate_efi_backup_bytes", return_value=512 * 1024**2)
@patch(
    "backup_engine.space_estimation.estimate_recovery_image_free_bytes",
    return_value=500 * 1024**3,
)
def test_estimate_required_bytes_uses_used_space(mock_free, mock_efi, mock_ntfs):
    mock_ntfs.return_value = (49 * 1024**3, "ntfs_used_space", None, {"probes": []})
    required = estimate_required_bytes(_layout())
    assert required < 80 * 1024**3
    assert required > 49 * 1024**3


@patch.object(run_backup_mod, "estimate_backup_space", return_value=_mock_estimate_ok())
@patch.object(run_backup_mod, "read_bitlocker_state", return_value="OFF")
@patch.object(run_backup_mod, "discover_layout")
@patch.object(run_backup_mod, "build_disk_metadata")
def test_dry_run_no_filesystem_writes(mock_disk, mock_discover, _mock_bl, _mock_est):
    mock_discover.return_value = (None, _layout())
    mock_disk.return_value = MagicMock(
        disk_guid="{disk}",
        disk_model="M",
        disk_serial="S",
        disk_size=1,
        windows_partition_uuid="{w}",
        efi_partition_uuid="{e}",
    )
    with patch("pathlib.Path.mkdir", side_effect=AssertionError("mkdir during dry-run")):
        with patch("pathlib.Path.write_text", side_effect=AssertionError("write during dry-run")):
            with patch.object(run_backup_mod, "run_command", side_effect=AssertionError("run_command during dry-run")):
                with patch.object(
                    run_backup_mod,
                    "finalize_backup_manifest",
                    side_effect=AssertionError("manifest during dry-run"),
                ):
                    result = plan_backup_run(WriteGuard(apply=False, confirmed=False))
    assert result.status == "PLANNED"


def test_expected_manifest_preview():
    disk = MagicMock(
        disk_guid="{d}",
        disk_model="M",
        disk_serial="S",
        disk_size=1,
        windows_partition_uuid="{w}",
        efi_partition_uuid="{e}",
    )
    manifest = build_expected_manifest(
        layout=_layout(),
        recovery_mount=Path("/mnt/recovery"),
        disk=disk,
    )
    assert manifest["image_files"]["windows"] == "images/system.pcl"
    assert manifest["sha256_hashes"]["images/system.pcl"] == "<computed-after-backup>"

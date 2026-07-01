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


@patch.object(run_backup_mod, "estimate_backup_space")
@patch.object(run_backup_mod, "read_bitlocker_state", return_value="OFF")
@patch.object(run_backup_mod, "discover_layout")
@patch.object(run_backup_mod, "build_disk_metadata")
def test_admin_backup_plan_uses_partclone_ignore_fschk(
    mock_disk,
    mock_discover,
    _mock_bl,
    mock_estimate,
):
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

    result = plan_backup_run(
        WriteGuard(apply=False, confirmed=False),
        backup_type="admin_compact",
    )

    assert "partclone.ntfs -I -c" in result.planned_commands["windows_partclone"]
    assert "partclone.ntfs -I -D" in result.planned_commands["windows_used_domain"]


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


def test_command_failure_detail_prefers_stderr():
    result = MagicMock(returncode=3, stderr="ntfsclone failed", stdout="")
    detail = run_backup_mod._command_failure_detail(
        "Windows backup",
        "partclone.ntfs -c -s /dev/nvme0n1p2 -o /tmp/out.pcl",
        result,
    )
    assert "exit 3" in detail
    assert "ntfsclone failed" in detail


def test_windows_backup_progress_multiplier_keeps_standard_boost():
    assert run_backup_mod._windows_backup_progress_multiplier("standard") == 2.6


def test_windows_backup_progress_multiplier_uses_raw_compact_progress():
    assert run_backup_mod._windows_backup_progress_multiplier("admin_compact") == 1.0


def test_emit_windows_progress_uses_supplied_multiplier():
    events = []
    ranges = {"windows": (2, 52)}

    run_backup_mod._emit_windows_backup_percent(
        events.append,
        percent=50,
        progress_ranges=ranges,
        progress_multiplier=1.0,
    )
    assert events[-1]["percent"] == 27

    run_backup_mod._emit_windows_backup_percent(
        events.append,
        percent=50,
        progress_ranges=ranges,
        progress_multiplier=2.6,
    )
    assert events[-1]["percent"] == 52


@patch.object(run_backup_mod, "run_command")
@patch.object(run_backup_mod, "_mounted_targets_for_device", return_value=["/run/recovery-runtime/mnt/windows_os"])
def test_ensure_windows_unmounted_unmounts_existing_mount(_mock_targets, mock_run_command):
    mock_run_command.return_value = MagicMock(returncode=0, stdout="", stderr="")

    ops = run_backup_mod._ensure_windows_unmounted(
        _layout(),
        WriteGuard(apply=True, confirmed=True),
    )

    mock_run_command.assert_called_once_with(
        ["umount", "/run/recovery-runtime/mnt/windows_os"],
        dry_run=False,
        confirmed=True,
    )
    assert ops == ["unmounted Windows partition from /run/recovery-runtime/mnt/windows_os"]


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
@patch.object(run_backup_mod, "mark_incomplete_backup")
@patch.object(run_backup_mod, "run_command")
@patch.object(run_backup_mod, "_run_partclone_streaming")
@patch.object(run_backup_mod, "verify_efi_artifact")
@patch.object(run_backup_mod, "verify_artifact_file")
@patch.object(run_backup_mod, "run_efi_backup_precheck")
@patch.object(run_backup_mod, "finalize_backup_manifest")
@patch.object(run_backup_mod, "_resolve_recovery_mount")
@patch.object(run_backup_mod, "discover_layout")
@patch.object(run_backup_mod, "read_bitlocker_state", return_value="OFF")
@patch.object(run_backup_mod, "build_disk_metadata")
@patch("backup_engine.backup_planner.has_valid_backup", return_value=False)
def test_apply_emits_progress_stages(
    _mock_valid,
    mock_disk,
    _mock_bl,
    mock_discover,
    mock_mount,
    mock_finalize,
    mock_efi_precheck,
    _mock_verify_artifact,
    _mock_verify_efi,
    mock_partclone,
    mock_cmd,
    _mock_mark_incomplete,
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
    mock_partclone.return_value = MagicMock(returncode=0, stdout="", stderr="")
    mock_finalize.return_value = ({}, "hash", Path("/mnt/recovery/manifests/recovery-manifest.json"))
    mock_efi_precheck.return_value = Path("/mnt/recovery/images/efi_backup.pcl")
    events = []

    with patch.object(run_backup_mod, "_ensure_windows_unmounted", return_value=[]):
        result = run_backup(
            apply=True,
            confirmed=True,
            progress_callback=events.append,
        )

    assert result.status == "COMPLETED"
    stages = [event["stage"] for event in events]
    assert "windows_backup" in stages
    assert "hash_generation" in stages
    assert "final_closeout" in stages
    assert stages[-1] == "complete"
    assert events[0]["percent"] == 0
    assert events[-1]["percent"] == 100
    command_lines = [" ".join(call.args[0]) for call in mock_cmd.call_args_list]
    assert any("partclone.ntfs -D" in line for line in command_lines)


@patch.object(WriteGuard, "mkdir")
@patch.object(run_backup_mod, "estimate_backup_space", return_value=_mock_estimate_ok())
@patch.object(run_backup_mod, "mark_incomplete_backup")
@patch.object(run_backup_mod, "run_command")
@patch.object(run_backup_mod, "verify_efi_artifact")
@patch.object(run_backup_mod, "verify_artifact_file")
@patch.object(run_backup_mod, "run_efi_backup_precheck")
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
    mock_efi_precheck,
    _mock_verify_artifact,
    _mock_verify_efi,
    mock_cmd,
    _mock_mark_incomplete,
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
    mock_finalize.return_value = ({}, "hash", Path("/mnt/recovery/manifests/recovery-manifest.json"))
    mock_efi_precheck.return_value = Path("/mnt/recovery/images/efi_backup.pcl")

    with patch.object(run_backup_mod, "_ensure_windows_unmounted", return_value=[]):
        result = run_backup(apply=True, confirmed=True)
    assert result.status == "COMPLETED"
    command_lines = [" ".join(call.args[0]) for call in mock_cmd.call_args_list]
    assert len(command_lines) == 4
    assert any("partclone.ntfs -D" in line for line in command_lines)
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
    assert manifest["image_files"]["windows"] == "images/windows_backup.pcl"
    assert (
        manifest["sha256_hashes"]["images/windows_backup.pcl"]
        == "<computed-after-backup>"
    )
    assert manifest["backup_type"] == "standard"


def test_expected_manifest_preview_supports_admin_backup_type():
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
        backup_type="admin_compact",
        admin_backup_metadata={
            "restore_baseline_bytes": 40 * 1024**3,
            "source_windows_partition_size_bytes": 100 * 1024**3,
            "compact_windows_partition_size_bytes": 40 * 1024**3,
        },
    )
    assert manifest["backup_type"] == "admin_compact"
    assert manifest["restore_baseline_bytes"] == 40 * 1024**3
    assert manifest["admin_backup"]["compact_windows_partition_size_bytes"] == 40 * 1024**3

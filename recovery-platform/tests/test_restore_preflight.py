"""Tests for restore preflight (non-destructive)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from recovery_runtime.gtk_ui.image_status import ImageStatus
from recovery_runtime.gtk_ui.restore_preflight import (
    DEV_MODE_DISABLED_MSG,
    RestorePreflightResult,
    build_preflight_summary,
    development_mode_enabled,
    run_restore_preflight,
)


def _image_status(**kwargs) -> ImageStatus:
    defaults = dict(
        recovery_image_partition_found=True,
        device="/dev/mock",
        filesystem="ext4",
        mount_point=Path("/mnt/mock"),
        mounted=True,
        manifest_exists=True,
        system_image_exists=True,
        esp_image_exists=True,
        hashes_exist=True,
        valid_backup_exists=True,
        errors=[],
    )
    defaults.update(kwargs)
    return ImageStatus(**defaults)


def _layout():
    layout = MagicMock()
    layout.windows.path = "/dev/nvme0n1p3"
    layout.efi.path = "/dev/nvme0n1p1"
    layout.recovery_image.path = "/dev/nvme0n1p5"
    layout.disk_path = "/dev/nvme0n1"
    layout.windows.size = 1
    layout.efi.size = 1
    layout.recovery_image.size = 1
    return layout


@patch("recovery_runtime.gtk_ui.restore_preflight.probe_recovery_image")
@patch("recovery_runtime.gtk_ui.restore_preflight.discover_layout")
@patch("recovery_runtime.gtk_ui.restore_preflight.read_bitlocker_state", return_value="OFF")
@patch("recovery_runtime.gtk_ui.restore_preflight._detect_bitlocker_skeleton", return_value=False)
@patch("recovery_runtime.gtk_ui.restore_preflight._run_hash_validation", return_value=(True, None))
def test_preflight_ok_development_mode(
    _hash,
    _bl,
    _bitlocker,
    mock_discover,
    mock_probe,
):
    mock_probe.return_value = _image_status()
    mock_discover.return_value = (None, _layout())

    result = run_restore_preflight()

    assert result.restore_preflight_ok is True
    assert result.restore_enabled is False
    assert result.development_mode is True
    assert DEV_MODE_DISABLED_MSG in result.warnings
    assert result.windows_partition_found is True
    assert result.efi_partition_found is True


@patch("recovery_runtime.gtk_ui.restore_preflight.probe_recovery_image")
@patch("recovery_runtime.gtk_ui.restore_preflight.discover_layout")
def test_recovery_image_missing(mock_discover, mock_probe):
    mock_probe.return_value = _image_status(
        recovery_image_partition_found=False,
        mounted=False,
        mount_point=None,
        valid_backup_exists=False,
        manifest_exists=False,
        hashes_exist=False,
        system_image_exists=False,
        esp_image_exists=False,
        errors=["RECOVERY_IMAGE partition not found"],
    )
    mock_discover.return_value = ("RECOVERY_IMAGE partition not found", None)

    result = run_restore_preflight()

    assert result.restore_preflight_ok is False
    assert result.recovery_image_found is False
    assert any("RECOVERY_IMAGE" in e for e in result.errors)


@patch("recovery_runtime.gtk_ui.restore_preflight.probe_recovery_image")
@patch("recovery_runtime.gtk_ui.restore_preflight.discover_layout")
@patch("recovery_runtime.gtk_ui.restore_preflight._detect_bitlocker_skeleton", return_value=False)
def test_valid_backup_missing(mock_bl, mock_discover, mock_probe):
    mock_probe.return_value = _image_status(valid_backup_exists=False, manifest_exists=False)
    mock_discover.return_value = (None, _layout())

    result = run_restore_preflight()

    assert result.restore_preflight_ok is False
    assert any("valid backup" in e for e in result.errors)


@patch("recovery_runtime.gtk_ui.restore_preflight.probe_recovery_image")
@patch("recovery_runtime.gtk_ui.restore_preflight.discover_layout")
@patch("recovery_runtime.gtk_ui.restore_preflight._detect_bitlocker_skeleton", return_value=False)
def test_manifest_and_images_missing(mock_bl, mock_discover, mock_probe):
    mock_probe.return_value = _image_status(
        manifest_exists=False,
        hashes_exist=False,
        system_image_exists=False,
        esp_image_exists=False,
        valid_backup_exists=False,
    )
    mock_discover.return_value = (None, _layout())

    result = run_restore_preflight()

    assert result.restore_preflight_ok is False
    assert any("manifest" in e for e in result.errors)
    assert any("windows_backup.pcl" in e for e in result.errors)
    assert any("efi_backup.pcl" in e for e in result.errors)


@patch("recovery_runtime.gtk_ui.restore_preflight.probe_recovery_image")
@patch("recovery_runtime.gtk_ui.restore_preflight.discover_layout")
@patch("recovery_runtime.gtk_ui.restore_preflight._detect_bitlocker_skeleton", return_value=True)
def test_bitlocker_detected(mock_bl, mock_discover, mock_probe):
    mock_probe.return_value = _image_status()
    mock_discover.return_value = (None, _layout())

    result = run_restore_preflight()

    assert result.bitlocker_detected is True
    assert result.restore_preflight_ok is False
    assert any("BitLocker" in e for e in result.errors)


@patch("recovery_runtime.gtk_ui.restore_preflight.probe_recovery_image")
@patch("recovery_runtime.gtk_ui.restore_preflight.discover_layout")
def test_unsupported_topology(mock_discover, mock_probe):
    mock_probe.return_value = _image_status()
    mock_discover.return_value = (
        "unsupported disk topology: multiple Windows OS partitions",
        None,
    )

    result = run_restore_preflight()

    assert result.unsupported_topology is True
    assert result.restore_preflight_ok is False
    assert result.windows_partition_found is False


@patch("recovery_runtime.gtk_ui.restore_preflight.probe_recovery_image")
@patch("recovery_runtime.gtk_ui.restore_preflight.discover_layout")
@patch("recovery_runtime.gtk_ui.restore_preflight._run_hash_validation", return_value=(False, "hash mismatch"))
@patch("recovery_runtime.gtk_ui.restore_preflight._detect_bitlocker_skeleton", return_value=False)
def test_hash_validation_failure(mock_hash, mock_bl, mock_discover, mock_probe):
    mock_probe.return_value = _image_status()
    mock_discover.return_value = (None, _layout())

    result = run_restore_preflight()

    assert result.sha256_validation_ready is False
    assert result.restore_preflight_ok is False
    assert any("hash" in e.lower() for e in result.errors)


def test_build_preflight_summary_includes_dev_mode():
    result = RestorePreflightResult(
        restore_preflight_ok=True,
        restore_enabled=False,
        development_mode=True,
        warnings=[DEV_MODE_DISABLED_MSG],
    )
    text = build_preflight_summary(result)
    assert "development mode" in text
    assert DEV_MODE_DISABLED_MSG in text


def test_development_mode_env_off():
    with patch.dict("os.environ", {"RECOVERIX_RESTORE_DEV": "0"}):
        assert development_mode_enabled() is False

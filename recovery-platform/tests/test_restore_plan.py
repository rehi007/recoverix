"""Tests for restore dry-run plan (non-destructive)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from recovery_runtime.gtk_ui.restore_plan import (
    RestorePlanResult,
    build_restore_plan_summary,
    run_restore_plan,
)
from recovery_runtime.gtk_ui.restore_preflight import (
    DEV_MODE_DISABLED_MSG,
    RestorePreflightResult,
)


def _preflight_ok(**kwargs) -> RestorePreflightResult:
    defaults = dict(
        restore_preflight_ok=True,
        restore_enabled=False,
        development_mode=True,
        recovery_image_found=True,
        valid_backup_exists=True,
        manifest_exists=True,
        hashes_exist=True,
        system_image_exists=True,
        esp_image_exists=True,
        windows_partition_found=True,
        efi_partition_found=True,
        recovery_image_mount="/mnt/mock",
        errors=[],
        warnings=[DEV_MODE_DISABLED_MSG],
    )
    defaults.update(kwargs)
    return RestorePreflightResult(**defaults)


def _layout():
    layout = MagicMock()
    layout.windows.path = "/dev/nvme0n1p3"
    layout.efi.path = "/dev/nvme0n1p1"
    layout.recovery_image.path = "/dev/nvme0n1p5"
    layout.disk_path = "/dev/nvme0n1"
    return layout


@patch("recovery_runtime.gtk_ui.restore_plan.run_restore_preflight")
@patch("recovery_runtime.gtk_ui.restore_plan.discover_layout")
@patch("recovery_runtime.gtk_ui.restore_plan.load_recovery_manifest")
@patch("recovery_runtime.gtk_ui.restore_plan._resolve_target_path", side_effect=lambda d: d)
def test_restore_plan_ok(mock_resolve, mock_manifest, mock_discover, mock_preflight):
    root = Path(tempfile.mkdtemp(prefix="recoverix-plan-"))
    (root / "images").mkdir()
    (root / "metadata").mkdir()
    (root / "images" / "efi_backup.pcl").write_bytes(b"efi")
    (root / "images" / "windows_backup.pcl").write_bytes(b"win")
    (root / "metadata" / "gpt_backup.bin").write_bytes(b"gpt")
    (root / "manifests").mkdir()
    (root / "manifests" / "recovery-manifest.json").write_text("{}", encoding="utf-8")

    mock_preflight.return_value = _preflight_ok(recovery_image_mount=str(root))
    mock_discover.return_value = (None, _layout())
    mock_manifest.return_value = {"device_id": "{test}"}

    result = run_restore_plan()

    assert result.restore_plan_ok is True
    assert result.restore_execution_enabled is False
    assert DEV_MODE_DISABLED_MSG in result.warnings
    assert len(result.targets) == 2
    assert any(t["name"] == "EFI" for t in result.targets)
    assert any(t["name"] == "Windows" for t in result.targets)
    assert len(result.dry_run_commands) == 2
    assert "partclone.fat" in result.dry_run_commands[0]
    assert "partclone.ntfs" in result.dry_run_commands[1]


@patch("recovery_runtime.gtk_ui.restore_plan.run_restore_preflight")
def test_preflight_failure_blocks_plan(mock_preflight):
    mock_preflight.return_value = _preflight_ok(
        restore_preflight_ok=False,
        errors=["valid backup does not exist"],
    )

    result = run_restore_plan()

    assert result.restore_plan_ok is False
    assert any("valid backup" in e for e in result.errors)


@patch("recovery_runtime.gtk_ui.restore_plan.run_restore_preflight")
@patch("recovery_runtime.gtk_ui.restore_plan.discover_layout")
def test_unsupported_topology(mock_discover, mock_preflight):
    mock_preflight.return_value = _preflight_ok()
    mock_discover.return_value = (
        "unsupported disk topology: multiple Windows OS partitions",
        None,
    )

    result = run_restore_plan()

    assert result.restore_plan_ok is False
    assert any("topology" in e for e in result.errors)


@patch("recovery_runtime.gtk_ui.restore_plan.run_restore_preflight")
@patch("recovery_runtime.gtk_ui.restore_plan.discover_layout")
@patch("recovery_runtime.gtk_ui.restore_plan.load_recovery_manifest")
@patch("recovery_runtime.gtk_ui.restore_plan._resolve_target_path", side_effect=lambda d: d)
def test_missing_images(mock_resolve, mock_manifest, mock_discover, mock_preflight):
    mock_preflight.return_value = _preflight_ok()
    mock_discover.return_value = (None, _layout())
    mock_manifest.return_value = {}

    result = run_restore_plan()

    assert result.restore_plan_ok is False
    assert any("missing image" in e for e in result.errors)


def test_build_restore_plan_summary_includes_dev_warning():
    plan = RestorePlanResult(
        restore_plan_ok=True,
        development_mode=True,
        restore_execution_enabled=False,
        warnings=[DEV_MODE_DISABLED_MSG],
        targets=[{"name": "EFI", "source": "/a", "target": "/b", "filesystem": "vfat", "operation": "x"}],
        dry_run_commands=["partclone.fat -r -s /a -o /b"],
    )
    text = build_restore_plan_summary(plan, preflight_ok=True)
    assert DEV_MODE_DISABLED_MSG in text
    assert "restore execution enabled: no" in text


def test_development_mode_execution_disabled():
    with patch("recovery_runtime.gtk_ui.restore_plan.development_mode_enabled", return_value=False):
        with patch("recovery_runtime.gtk_ui.restore_plan.run_restore_preflight") as mock_pf:
            mock_pf.return_value = _preflight_ok(development_mode=False, warnings=[])
            with patch("recovery_runtime.gtk_ui.restore_plan.discover_layout", return_value=(None, None)):
                result = run_restore_plan()
    assert result.restore_execution_enabled is False

"""Tests for boot_manager.efi_installer."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from boot_manager.efi_installer import (
    EfiFileAction,
    _assert_safe_destination,
    _assets_dir,
    build_backup_plan,
    build_file_actions,
    resolve_esp_mount,
    run_efi_installer,
)
from common.errors import BitLockerActiveError, RecoveryError
from partition_manager.models import PartitionRecord, PartitionState


def _efi_record(letter: str = "E") -> PartitionRecord:
    return PartitionRecord(
        disk_number=2,
        partition_number=1,
        role="efi",
        state=PartitionState.FOUND.value,
        file_system="FAT32",
        drive_letter=letter,
    )


def test_placeholder_assets_exist():
    assets = _assets_dir()
    for name in ("shimx64.efi", "grubx64.efi", "grub.cfg"):
        assert (assets / name).is_file()


def test_assert_safe_destination_rejects_bootmgfw():
    try:
        _assert_safe_destination(r"EFI\Microsoft\Boot\bootmgfw.efi")
    except RecoveryError:
        pass
    else:
        raise AssertionError("expected RecoveryError")


def test_assert_safe_destination_allows_recovery_boot():
    _assert_safe_destination(r"EFI\RecoveryBoot\shimx64.efi")


def test_resolve_esp_mount_from_letter():
    mount = resolve_esp_mount(_efi_record("E"))
    assert mount == "E:\\"


def test_build_file_actions():
    with tempfile.TemporaryDirectory() as tmp:
        actions = build_file_actions(tmp + "\\")
        assert len(actions) == 3
        for action in actions:
            assert "RecoveryBoot" in action.destination
            assert "Microsoft" not in action.destination


def test_build_backup_plan():
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp) / "esp"
        dest = esp / "EFI" / "RecoveryBoot"
        dest.mkdir(parents=True)
        existing = dest / "grub.cfg"
        existing.write_text("old", encoding="utf-8")

        actions = [
            EfiFileAction(
                source=str(_assets_dir() / "grub.cfg"),
                destination=str(existing),
            )
        ]
        backup_dir = Path(tmp) / "backup"
        plan = build_backup_plan(actions, backup_dir=backup_dir)
        assert any("backup" in item for item in plan)


@patch("boot_manager.efi_installer.discover_esp")
@patch("boot_manager.efi_installer.read_bitlocker_state")
def test_dry_run_planned(mock_bitlocker, mock_esp):
    with tempfile.TemporaryDirectory() as tmp:
        mock_bitlocker.return_value = "OFF"
        mock_esp.return_value = (tmp, _efi_record(), "")

        result = run_efi_installer(dry_run=True, apply=False)
        assert result.status == "PLANNED"
        assert result.dry_run is True
        assert result.apply is False
        assert len(result.file_actions) == 3
        assert not (Path(tmp) / "EFI" / "RecoveryBoot" / "shimx64.efi").exists()


@patch("boot_manager.efi_installer.discover_esp")
@patch("boot_manager.efi_installer.read_bitlocker_state")
def test_apply_installs_placeholders(mock_bitlocker, mock_esp):
    with tempfile.TemporaryDirectory() as tmp:
        mock_bitlocker.return_value = "OFF"
        esp = Path(tmp) / "esp"
        esp.mkdir()
        bootmgfw = esp / "EFI" / "Microsoft" / "Boot"
        bootmgfw.mkdir(parents=True)
        original = bootmgfw / "bootmgfw.efi"
        original.write_bytes(b"WINDOWS_BOOTMGFW_ORIGINAL")

        mock_esp.return_value = (str(esp), _efi_record(), "")

        result = run_efi_installer(dry_run=False, apply=True)
        assert result.status == "APPLIED"
        assert (esp / "EFI" / "RecoveryBoot" / "shimx64.efi").is_file()
        assert original.read_bytes() == b"WINDOWS_BOOTMGFW_ORIGINAL"


@patch("boot_manager.efi_installer.discover_esp")
@patch("boot_manager.efi_installer.read_bitlocker_state")
def test_apply_rejects_bitlocker(mock_bitlocker, mock_esp):
    with tempfile.TemporaryDirectory() as tmp:
        mock_bitlocker.return_value = "ON"
        mock_esp.return_value = (tmp, _efi_record(), "")

        try:
            run_efi_installer(dry_run=False, apply=True)
        except BitLockerActiveError:
            pass
        else:
            raise AssertionError("expected BitLockerActiveError")


@patch("boot_manager.efi_installer.discover_esp")
def test_esp_missing_fails(mock_esp):
    mock_esp.return_value = (None, None, "EFI System Partition not found")
    result = run_efi_installer(dry_run=True, apply=False)
    assert result.status == "FAIL"


def test_json_output_shape():
    result = run_efi_installer(dry_run=True, apply=False)
    payload = json.loads(result.to_json())
    assert "backup_plan" in payload
    assert "planned_actions" in payload

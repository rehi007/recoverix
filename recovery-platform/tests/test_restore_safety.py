"""Tests for restore_engine restore safety gate (step 13-A)."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import restore_engine.restore_safety as safety_mod
from backup_engine.backup_planner import _DiscoveredLayout
from backup_engine.manifest import DiskMetadata, ManifestContext, finalize_backup_manifest
from common.errors import (
    BitLockerActiveError,
    ConfirmationRequiredError,
    InvalidConfirmationPhraseError,
    RestoreEnvironmentError,
    RestoreSafetyError,
)
from recovery_runtime.discover import DiscoveredVolume
from restore_engine.confirmation import (
    RESTORE_CONFIRMATION_PHRASE,
    format_target_disk_display,
    require_confirmation_phrase,
    verify_confirmation_phrase,
)
from restore_engine.restore_planner import RestoreCheckResult


def _disk() -> DiskMetadata:
    return DiskMetadata(
        disk_guid="{11111111-1111-1111-1111-111111111111}",
        disk_model="TestDisk",
        disk_serial="SN123",
        disk_size=1_000_000_000_000,
        windows_partition_uuid="{22222222-2222-2222-2222-222222222222}",
        efi_partition_uuid="{33333333-3333-3333-3333-333333333333}",
    )


def _layout(mount: str) -> _DiscoveredLayout:
    return _DiscoveredLayout(
        windows=DiscoveredVolume(
            name="nvme0n1p3",
            path="/dev/nvme0n1p3",
            label=None,
            fstype="ntfs",
            size=1,
            mountpoint=None,
            device_type="part",
        ),
        efi=DiscoveredVolume(
            name="nvme0n1p1",
            path="/dev/nvme0n1p1",
            label=None,
            fstype="vfat",
            size=1,
            mountpoint=None,
            device_type="part",
        ),
        recovery_image=DiscoveredVolume(
            name="nvme0n1p5",
            path="/dev/nvme0n1p5",
            label="RECOVERY_IMAGE",
            fstype="ext4",
            size=1,
            mountpoint=mount,
            device_type="part",
        ),
        disk_path="/dev/nvme0n1",
        volumes=[],
    )


def _populate_backup(root: Path) -> None:
    files = {
        "metadata/gpt_backup.bin": b"gpt",
        "images/efi_backup.pcl": b"efi",
        "images/windows_backup.pcl": b"win",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    finalize_backup_manifest(ManifestContext(recovery_root=root, disk=_disk()))


def _runtime_ok():
    return safety_mod.RestoreSafetyCheck(
        name="recovery_runtime",
        passed=True,
        details={"recovery_linux_found": True},
    )


def _compatible_target_ok() -> RestoreCheckResult:
    return RestoreCheckResult(
        name="compatible_target_disk",
        passed=True,
        status="COMPATIBLE",
        reason="target disk accepted for compatible restore",
        details={
            "partclone_no_check_required": False,
            "ntfs_post_resize_required": False,
        },
    )


def _geometry_ok() -> RestoreCheckResult:
    return RestoreCheckResult(
        name="windows_target_geometry",
        passed=True,
        status="PASS",
    )


def test_apply_without_confirm_raises():
    try:
        safety_mod.authorize_restore_execution(apply=True, confirmed=False)
    except ConfirmationRequiredError:
        pass
    else:
        raise AssertionError("expected ConfirmationRequiredError")


def test_apply_alone_not_allowed():
    result = safety_mod.evaluate_restore_safety(apply=True, confirmed=False)
    assert result.allowed is False
    assert any(c["name"] == "apply_confirm" for c in result.checks)


@patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok())
@patch.object(safety_mod, "read_bitlocker_state", return_value="ON")
def test_bitlocker_on_rejected(_mock_bl, _mock_rt):
    result = safety_mod.evaluate_restore_safety(
        apply=True,
        confirmed=True,
        confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
    )
    assert result.allowed is False
    try:
        safety_mod.authorize_restore_execution(
            apply=True,
            confirmed=True,
            confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
        )
    except BitLockerActiveError:
        pass
    else:
        raise AssertionError("expected BitLockerActiveError")


@patch.object(safety_mod, "discover_recovery_volumes", return_value=(None, None, []))
def test_not_recovery_runtime_rejected(_mock_discover):
    result = safety_mod.evaluate_restore_safety(
        apply=True,
        confirmed=True,
        confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
    )
    assert result.allowed is False
    try:
        safety_mod.authorize_restore_execution(
            apply=True,
            confirmed=True,
            confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
        )
    except RestoreEnvironmentError:
        pass
    else:
        raise AssertionError("expected RestoreEnvironmentError")


@patch("sys.platform", "win32")
def test_windows_forbidden():
    result = safety_mod.evaluate_restore_safety(
        apply=True,
        confirmed=True,
        confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
    )
    assert result.allowed is False
    try:
        safety_mod.authorize_restore_execution(
            apply=True,
            confirmed=True,
            confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
        )
    except RestoreEnvironmentError:
        pass
    else:
        raise AssertionError("expected RestoreEnvironmentError")


def test_invalid_confirmation_phrase():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(str(root))
        with patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok()):
            with patch.object(safety_mod, "read_bitlocker_state", return_value="OFF"):
                with patch.object(safety_mod, "discover_layout", return_value=(None, layout)):
                    with patch.object(safety_mod, "build_disk_metadata", return_value=_disk()):
                        result = safety_mod.evaluate_restore_safety(
                            apply=True,
                            confirmed=True,
                            confirmation_phrase="WRONG",
                            recovery_root=root,
                        )
        assert result.allowed is False
        assert result.phrase_verified is False


def test_confirmation_phrase_exact_match():
    assert verify_confirmation_phrase(RESTORE_CONFIRMATION_PHRASE)
    assert not verify_confirmation_phrase("restore this device")
    assert not verify_confirmation_phrase(None)


def test_target_disk_display_shows_identifiers():
    disk = _disk()
    lines = format_target_disk_display(disk, manifest_device_id="abc")
    text = "\n".join(lines)
    assert disk.disk_guid in text
    assert disk.disk_serial in text
    assert disk.disk_model in text
    assert "abc" in text


def test_require_confirmation_phrase_raises():
    try:
        require_confirmation_phrase("nope", disk=_disk())
    except InvalidConfirmationPhraseError:
        pass
    else:
        raise AssertionError("expected InvalidConfirmationPhraseError")


@patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok())
@patch.object(safety_mod, "read_bitlocker_state", return_value="OFF")
def test_incomplete_backup_rejected(_mock_bl, _mock_rt):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        from backup_engine.backup_state import mark_incomplete_backup

        mark_incomplete_backup(root, "failed")
        layout = _layout(str(root))
        with patch.object(safety_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(safety_mod, "build_disk_metadata", return_value=_disk()):
                result = safety_mod.evaluate_restore_safety(
                    apply=True,
                    confirmed=True,
                    confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                    recovery_root=root,
                )
    assert result.allowed is False
    assert any(c["name"] == "incomplete_backup" and not c["passed"] for c in result.checks)


@patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok())
@patch.object(safety_mod, "read_bitlocker_state", return_value="OFF")
def test_validate_restore_failure_rejected(_mock_bl, _mock_rt):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(str(root))
        wrong = DiskMetadata(
            disk_guid="{other}",
            disk_model="X",
            disk_serial="Y",
            disk_size=1,
            windows_partition_uuid="{w}",
            efi_partition_uuid="{e}",
        )
        with patch.object(safety_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(safety_mod, "build_disk_metadata", return_value=wrong):
                result = safety_mod.evaluate_restore_safety(
                    apply=True,
                    confirmed=True,
                    confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                    recovery_root=root,
                )
    assert result.allowed is False
    failed = [c for c in result.checks if c["name"] in ("device_id", "validate_restore") and not c["passed"]]
    assert failed


@patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok())
@patch.object(safety_mod, "read_bitlocker_state", return_value="OFF")
def test_compatible_restore_authorizes_device_mismatch(_mock_bl, _mock_rt):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(str(root))
        replacement_disk = DiskMetadata(
            disk_guid="{99999999-9999-9999-9999-999999999999}",
            disk_model="Replacement",
            disk_serial="REPLACED",
            disk_size=1_000_000_000_000,
            windows_partition_uuid="{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}",
            efi_partition_uuid="{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}",
            )
        with patch.object(safety_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(safety_mod, "build_disk_metadata", return_value=replacement_disk):
                with patch.object(Path, "exists", return_value=True):
                    with patch.object(
                        safety_mod,
                        "verify_windows_target_geometry",
                        return_value=_geometry_ok(),
                    ):
                        with patch.object(
                            safety_mod,
                            "verify_compatible_target_disk",
                            return_value=_compatible_target_ok(),
                        ):
                            result = safety_mod.authorize_restore_execution(
                                apply=True,
                                confirmed=True,
                                confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                                recovery_root=root,
                                compatible_restore=True,
                            )
    assert result.allowed is True
    assert result.compatible_restore is True
    assert result.restore_mode == "compatible"


@patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok())
@patch.object(safety_mod, "read_bitlocker_state", return_value="OFF")
def test_all_checks_pass_authorized(_mock_bl, _mock_rt):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(str(root))
        with patch.object(safety_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(safety_mod, "build_disk_metadata", return_value=_disk()):
                with patch.object(
                    safety_mod,
                    "verify_windows_target_geometry",
                    return_value=_geometry_ok(),
                ):
                    result = safety_mod.authorize_restore_execution(
                        apply=True,
                        confirmed=True,
                        confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                        recovery_root=root,
                    )
    assert result.allowed is True
    assert result.phrase_verified is True
    assert result.target_disk["disk_guid"] == _disk().disk_guid


@patch.object(safety_mod, "validate_restore", side_effect=RuntimeError("boom"))
@patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok())
@patch.object(safety_mod, "read_bitlocker_state", return_value="OFF")
def test_validation_exception_fail_closed(_mock_bl, _mock_rt, _mock_val):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(str(root))
        with patch.object(safety_mod, "discover_layout", return_value=(None, layout)):
            with patch.object(safety_mod, "build_disk_metadata", return_value=_disk()):
                result = safety_mod.evaluate_restore_safety(
                    apply=True,
                    confirmed=True,
                    confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                    recovery_root=root,
                )
    assert result.allowed is False
    try:
        with patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok()):
            with patch.object(safety_mod, "read_bitlocker_state", return_value="OFF"):
                with patch.object(safety_mod, "discover_layout", return_value=(None, layout)):
                    with patch.object(safety_mod, "build_disk_metadata", return_value=_disk()):
                        safety_mod.authorize_restore_execution(
            apply=True,
            confirmed=True,
                            confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                            recovery_root=root,
                        )
    except RestoreSafetyError:
        pass
    else:
        raise AssertionError("expected RestoreSafetyError")

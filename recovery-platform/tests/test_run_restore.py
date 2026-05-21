"""Tests for restore_engine.run_restore and restore_executor."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import restore_engine.restore_executor as executor_mod
import restore_engine.restore_safety as safety_mod
import restore_engine.run_restore as run_restore_mod
from backup_engine.backup_planner import _DiscoveredLayout
from backup_engine.manifest import DiskMetadata, ManifestContext, finalize_backup_manifest
from common.errors import (
    BitLockerActiveError,
    ConfirmationRequiredError,
    InvalidConfirmationPhraseError,
    RestoreSafetyError,
)
from recovery_runtime.discover import DiscoveredVolume
from restore_engine.confirmation import RESTORE_CONFIRMATION_PHRASE
from restore_engine.restore_executor import RestoreExecutionContext, RestoreExecutor
from restore_engine.restore_safety import RestoreSafetyResult
from restore_engine.restore_state import load_recovery_state


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
        "images/efi.pcl": b"efi",
        "images/system.pcl": b"win",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    finalize_backup_manifest(ManifestContext(recovery_root=root, disk=_disk()))


def _safety_ok() -> RestoreSafetyResult:
    return RestoreSafetyResult(
        allowed=True,
        status="PASS",
        apply=True,
        confirmed=True,
        phrase_verified=True,
        target_disk={
            "disk_guid": _disk().disk_guid,
            "disk_serial": _disk().disk_serial,
            "disk_model": _disk().disk_model,
        },
    )


def _runtime_ok():
    return safety_mod.RestoreSafetyCheck(
        name="recovery_runtime",
        passed=True,
    )


def test_cli_apply_only_rejected():
    assert run_restore_mod.main(["--apply"]) == 2


def test_cli_missing_phrase_rejected():
    assert run_restore_mod.main(["--apply", "--confirm"]) == 2


def test_authorize_failure_blocks_executor():
    safety = RestoreSafetyResult(
        allowed=False,
        status="REJECTED",
        reason="blocked",
    )
    try:
        RestoreExecutor(
            RestoreExecutionContext(
                safety=safety,
                recovery_root=Path("/tmp/r"),
                layout=_layout("/tmp/r"),
                disk=_disk(),
                confirmed=True,
            )
        )
    except RestoreSafetyError:
        pass
    else:
        raise AssertionError("expected RestoreSafetyError")


@patch.object(executor_mod, "run_command")
@patch.object(executor_mod, "validate_restore")
def test_authorize_failure_no_partclone(mock_validate, mock_cmd):
    mock_validate.return_value = MagicMock(allowed=True)
    safety = RestoreSafetyResult(allowed=False, status="REJECTED")
    try:
        RestoreExecutor(
            RestoreExecutionContext(
                safety=safety,
                recovery_root=Path("/tmp/x"),
                layout=_layout("/tmp/x"),
                disk=_disk(),
                confirmed=True,
            )
        ).execute()
    except RestoreSafetyError:
        mock_cmd.assert_not_called()
    else:
        raise AssertionError("expected RestoreSafetyError")


@patch.object(run_restore_mod, "authorize_restore_execution")
def test_validate_restore_failure_blocks(mock_auth):
    mock_auth.return_value = _safety_ok()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        ctx = RestoreExecutionContext(
            safety=_safety_ok(),
            recovery_root=root,
            layout=_layout(str(root)),
            disk=_disk(),
            confirmed=True,
        )
        with patch.object(executor_mod, "validate_restore") as mock_val:
            mock_val.return_value = MagicMock(allowed=False, reason="hash mismatch")
            result = RestoreExecutor(ctx).execute()
        assert result.success is False
        state = load_recovery_state(root)
        assert state.rollback_required is True


@patch.object(run_restore_mod, "authorize_restore_execution", side_effect=InvalidConfirmationPhraseError("bad"))
def test_phrase_mismatch_cli(_mock_auth):
    assert (
        run_restore_mod.main(
            ["--apply", "--confirm", "--phrase", "WRONG"],
        )
        == 2
    )


@patch.object(run_restore_mod, "authorize_restore_execution", side_effect=BitLockerActiveError("ON"))
def test_bitlocker_blocks_cli(_mock_auth):
    assert (
        run_restore_mod.main(
            [
                "--apply",
                "--confirm",
                "--phrase",
                RESTORE_CONFIRMATION_PHRASE,
            ],
        )
        == 1
    )


@patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok())
@patch.object(safety_mod, "read_bitlocker_state", return_value="OFF")
def test_device_id_mismatch_blocks(_mock_bl, _mock_rt):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        wrong = DiskMetadata(
            disk_guid="{other}",
            disk_model="X",
            disk_serial="Y",
            disk_size=1,
            windows_partition_uuid="{w}",
            efi_partition_uuid="{e}",
        )
        with patch.object(safety_mod, "discover_layout", return_value=(None, _layout(str(root)))):
            with patch.object(safety_mod, "build_disk_metadata", return_value=wrong):
                result = safety_mod.evaluate_restore_safety(
                    apply=True,
                    confirmed=True,
                    confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                    recovery_root=root,
                )
    assert result.allowed is False


@patch("restore_engine.run_restore.RestoreExecutor")
@patch("restore_engine.run_restore.build_execution_context")
@patch("restore_engine.run_restore.authorize_restore_execution")
def test_authorized_enters_executor(mock_auth, mock_ctx, mock_executor_cls):
    mock_auth.return_value = _safety_ok()
    mock_ctx.return_value = MagicMock()
    instance = MagicMock()
    instance.execute.return_value = MagicMock(
        success=True,
        status="COMPLETED",
        current_stage="restore_complete",
        reason=None,
        to_dict=lambda: {"success": True},
    )
    mock_executor_cls.return_value = instance
    code = run_restore_mod.main(
        [
            "--apply",
            "--confirm",
            "--phrase",
            RESTORE_CONFIRMATION_PHRASE,
            "--json",
        ],
    )
    assert code == 0
    mock_executor_cls.assert_called_once()
    instance.execute.assert_called_once()


def test_restore_failure_sets_rollback_required():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        ctx = RestoreExecutionContext(
            safety=_safety_ok(),
            recovery_root=root,
            layout=_layout(str(root)),
            disk=_disk(),
            confirmed=True,
        )
        executor = RestoreExecutor(ctx)
        with patch.object(executor, "_stage_backup_efi", side_effect=RuntimeError("efi backup failed")):
            with patch.object(executor_mod, "validate_restore") as mock_val:
                mock_val.return_value = MagicMock(allowed=True)
                result = executor.execute()
        assert result.rollback_required is True
        state = load_recovery_state(root)
        assert state.rollback_required is True
        assert state.restore_in_progress is False


def test_restore_success_clears_in_progress():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        layout = _layout(str(root))
        ctx = RestoreExecutionContext(
            safety=_safety_ok(),
            recovery_root=root,
            layout=layout,
            disk=_disk(),
            confirmed=True,
        )
        executor = RestoreExecutor(ctx)
        stage_patches = [
            "_stage_backup_efi",
            "_stage_backup_gpt",
            "_stage_ensure_windows_unmounted",
            "_stage_partclone_windows",
            "_stage_restore_recovery_boot_efi",
            "_stage_verify_windows_boot_manager",
            "_stage_repair_bootorder",
            "_stage_log_integrity",
            "_try_unmount_efi",
        ]
        with patch.object(executor_mod, "validate_restore") as mock_val:
            mock_val.return_value = MagicMock(allowed=True)
            patches = [patch.object(executor, name) for name in stage_patches]
            for item in patches:
                item.start()
            try:
                result = executor.execute()
            finally:
                for item in patches:
                    item.stop()
        assert result.success is True
        state = load_recovery_state(root)
        assert state.restore_in_progress is False
        assert state.restore_success is True


@patch.object(executor_mod, "run_command")
def test_incomplete_backup_blocks_via_safety(mock_cmd):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_backup(root)
        from backup_engine.backup_state import mark_incomplete_backup

        mark_incomplete_backup(root, "failed")
        with patch.object(safety_mod, "is_recovery_runtime_environment", return_value=_runtime_ok()):
            with patch.object(safety_mod, "read_bitlocker_state", return_value="OFF"):
                with patch.object(safety_mod, "discover_layout", return_value=(None, _layout(str(root)))):
                    with patch.object(safety_mod, "build_disk_metadata", return_value=_disk()):
                        result = safety_mod.evaluate_restore_safety(
                            apply=True,
                            confirmed=True,
                            confirmation_phrase=RESTORE_CONFIRMATION_PHRASE,
                            recovery_root=root,
                        )
    assert result.allowed is False
    mock_cmd.assert_not_called()

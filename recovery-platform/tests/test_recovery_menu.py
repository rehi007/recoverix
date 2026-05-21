"""Tests for Recovery Runtime menu integration."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import recovery_runtime.actions as actions_mod
import recovery_runtime.menu as menu_mod
import recovery_runtime.runtime_context as ctx_mod
from backup_engine.hash import sha256_file
from backup_engine.manifest import DiskMetadata, ManifestContext, finalize_backup_manifest
from recovery_runtime.runtime_context import MenuAvailability, RuntimeContext
from restore_engine.confirmation import RESTORE_CONFIRMATION_PHRASE
from recovery_runtime.ui_helpers import render_log_menu, verify_phrase


def _disk() -> DiskMetadata:
    return DiskMetadata(
        disk_guid="{11111111-1111-1111-1111-111111111111}",
        disk_model="TestDisk",
        disk_serial="SN123",
        disk_size=1_000_000_000_000,
        windows_partition_uuid="{22222222-2222-2222-2222-222222222222}",
        efi_partition_uuid="{33333333-3333-3333-3333-333333333333}",
    )


def _populate_valid_backup(root: Path) -> None:
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


def _ctx_with_menu(**kwargs) -> RuntimeContext:
    menu = MenuAvailability(**kwargs)
    ctx = RuntimeContext(
        recovery_root=Path("/mnt/recovery"),
        menu=menu,
        runtime_state=MagicMock(summary_lines=lambda: []),
    )
    return ctx


def test_valid_backup_disables_backup_menu():
    ctx = _ctx_with_menu(
        backup_executable=False,
        backup_reason="기존 valid backup 존재",
        restore_executable=False,
        restore_reason="rollback_required",
        delete_executable=True,
    )
    msg = actions_mod.run_backup_action(ctx, input_func=lambda _: "y")
    assert "불가" in msg or "disabled" in msg.lower()


@patch.object(ctx_mod, "validate_restore")
def test_validate_restore_fail_disables_restore(mock_validate):
    mock_validate.return_value = MagicMock(
        allowed=False,
        status="REJECTED",
        reason="hash mismatch; restore forbidden",
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root)
        ctx = RuntimeContext(recovery_root=root)
        with patch("recovery_runtime.discover.discover_recovery_volumes") as mock_disc:
            mock_disc.return_value = (MagicMock(mountpoint=str(root), path="/dev/p5", label="RECOVERY_IMAGE", size=1), MagicMock(path="/dev/p6", label="RECOVERY_LINUX"), [])
            layout = MagicMock(
                recovery_image=MagicMock(mountpoint=str(root)),
                disk_path="/dev/nvme0n1",
                efi=MagicMock(path="/dev/nvme0n1p1"),
                windows=MagicMock(path="/dev/nvme0n1p3"),
            )
            with patch.object(ctx_mod, "discover_layout", return_value=(None, layout)):
                with patch.object(
                    ctx_mod,
                    "verify_windows_boot_manager",
                    return_value=MagicMock(passed=True, status="PLANNED"),
                ):
                    with patch.object(ctx_mod, "build_disk_metadata", return_value=_disk()):
                        with patch.object(ctx_mod, "read_bitlocker_state", return_value="OFF"):
                            with patch.object(ctx_mod, "read_secure_boot_state", return_value="UNKNOWN"):
                                with patch.object(ctx_mod, "read_firmware_boot", return_value=MagicMock(boot_order=[])):
                                    with patch.object(ctx_mod, "build_restore_plan") as mock_plan:
                                        mock_plan.return_value = MagicMock(
                                            restore_allowed=False,
                                            reason="hash mismatch",
                                        )
                                        with patch.object(ctx_mod, "has_valid_recovery_backup", return_value=True):
                                            with patch.object(ctx_mod, "has_incomplete_backup", return_value=False):
                                                with patch.object(ctx_mod, "apply_persisted_recovery_state", return_value=MagicMock()):
                                                    ctx.refresh()
        assert ctx.menu.restore_executable is False
        assert "hash" in (ctx.menu.restore_reason or "").lower() or "mismatch" in (ctx.menu.restore_reason or "").lower()


def test_incomplete_backup_disables_restore():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        from backup_engine.backup_state import mark_incomplete_backup

        mark_incomplete_backup(root, "failed")
        ctx = RuntimeContext(recovery_root=root)
        with patch.object(ctx_mod, "has_incomplete_backup", return_value=True):
            with patch.object(ctx_mod, "has_valid_recovery_backup", return_value=False):
                with patch("recovery_runtime.discover.discover_recovery_volumes") as mock_disc:
                    mock_disc.return_value = (
                        MagicMock(mountpoint=str(root), path="/dev/p5", label="RECOVERY_IMAGE", size=1),
                        MagicMock(path="/dev/p6", label="RECOVERY_LINUX"),
                        [],
                    )
                    with patch.object(ctx_mod, "validate_restore", return_value=MagicMock(allowed=False, reason="incomplete")):
                        with patch.object(ctx_mod, "build_restore_plan", return_value=MagicMock(restore_allowed=False)):
                            with patch.object(ctx_mod, "discover_layout", return_value=(None, None)):
                                with patch.object(ctx_mod, "read_bitlocker_state", return_value="OFF"):
                                    with patch.object(ctx_mod, "read_secure_boot_state", return_value="UNKNOWN"):
                                        with patch.object(ctx_mod, "read_firmware_boot", return_value=MagicMock(boot_order=[])):
                                            with patch.object(ctx_mod, "apply_persisted_recovery_state", return_value=MagicMock()):
                                                ctx.refresh()
        assert ctx.menu.restore_executable is False


def test_bitlocker_on_disables_backup_and_restore():
    ctx = _ctx_with_menu(
        backup_executable=False,
        backup_reason="BitLocker ON",
        restore_executable=False,
        restore_reason="BitLocker ON",
    )
    assert "BitLocker" in (ctx.menu.backup_reason or "")
    msg = actions_mod.run_restore_action(ctx, input_func=lambda _: RESTORE_CONFIRMATION_PHRASE)
    assert "불가" in msg or "disabled" in msg.lower()


def test_confirmation_mismatch_blocks_backup():
    ctx = _ctx_with_menu(backup_executable=True, restore_executable=False)
    with patch.object(actions_mod, "run_backup") as mock_backup:
        msg = actions_mod.run_backup_action(
            ctx,
            input_func=lambda prompt: "y" if "진행" in prompt else "WRONG",
        )
    mock_backup.assert_not_called()
    assert "불일치" in msg or "차단" in msg


def test_restore_phrase_mismatch_blocks_executor():
    ctx = _ctx_with_menu(restore_executable=True)
    ctx.recovery_root = Path("/tmp/r")
    ctx.current_disk = _disk()
    with patch.object(actions_mod, "authorize_restore_execution") as mock_auth:
        msg = actions_mod.run_restore_action(
            ctx,
            input_func=lambda prompt: "WRONG",
        )
    mock_auth.assert_not_called()
    assert "불일치" in msg or "차단" in msg


def test_missing_logs_graceful():
    with tempfile.TemporaryDirectory() as tmp:
        msg = render_log_menu(Path(tmp), ["restore.log"], input_func=lambda _: "0")
    assert "없습니다" in msg or "종료" in msg


def test_menu_exception_does_not_exit_runtime():
    ctx = RuntimeContext(runtime_state=MagicMock(summary_lines=lambda: []))
    calls = {"n": 0}

    def flaky_refresh():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    ctx.refresh = flaky_refresh  # type: ignore[method-assign]
    with patch.object(menu_mod, "render_menu"):
        with patch.object(menu_mod, "read_choice", side_effect=["1", "6"]):
            with patch.object(menu_mod, "show_status_action", return_value="ok"):
                code = menu_mod.run_menu_loop(ctx, input_func=lambda _: "1")
    assert code == 0


@patch.object(actions_mod, "run_backup")
@patch.object(actions_mod, "create_backup_plan")
@patch.object(actions_mod, "plan_backup_run")
def test_backup_action_success(mock_plan_run, mock_create, mock_run):
    mock_create.return_value = MagicMock(status="PLANNED", reason=None, can_backup=True)
    mock_plan_run.return_value = MagicMock(estimated_required_bytes=1024)
    mock_run.return_value = MagicMock(status="COMPLETED", reason=None)
    ctx = _ctx_with_menu(backup_executable=True)

    def fake_input(prompt: str) -> str:
        if "진행" in prompt:
            return "y"
        if "confirmation" in prompt:
            return "START BACKUP"
        return ""

    from recovery_runtime.ui_helpers import BACKUP_CONFIRMATION_PHRASE

    msg = actions_mod.run_backup_action(ctx, input_func=fake_input)
    assert "완료" in msg
    mock_run.assert_called_once_with(apply=True, confirmed=True)


def test_delete_wrong_phrase_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root)
        ctx = _ctx_with_menu(delete_executable=True)
        ctx.recovery_root = root
        msg = actions_mod.delete_backup_action(
            ctx,
            input_func=lambda prompt: "y" if "삭제" in prompt else "WRONG",
        )
    assert "불일치" in msg or "차단" in msg


def test_has_valid_recovery_backup():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert ctx_mod.has_valid_recovery_backup(root) is False
        _populate_valid_backup(root)
        assert ctx_mod.has_valid_recovery_backup(root) is True


def test_verify_restore_phrase():
    from recovery_runtime.ui_helpers import BACKUP_CONFIRMATION_PHRASE, DELETE_CONFIRMATION_PHRASE

    assert verify_phrase(RESTORE_CONFIRMATION_PHRASE, RESTORE_CONFIRMATION_PHRASE)
    assert not verify_phrase("wrong", DELETE_CONFIRMATION_PHRASE)
    assert verify_phrase(BACKUP_CONFIRMATION_PHRASE, BACKUP_CONFIRMATION_PHRASE)

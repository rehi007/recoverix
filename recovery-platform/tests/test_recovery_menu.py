"""Tests for Recovery Runtime menu integration."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import recovery_runtime.actions as actions_mod
import recovery_runtime.backup_admin as backup_admin_mod
from recovery_runtime.admin_bridge import decode_json_payload
import recovery_runtime.menu as menu_mod
import recovery_runtime.runtime_context as ctx_mod
from backup_engine.hash import sha256_file
from backup_engine.manifest import DiskMetadata, ManifestContext, finalize_backup_manifest
from recovery_runtime.runtime_context import MenuAvailability, RuntimeContext
from restore_engine.confirmation import RESTORE_CONFIRMATION_PHRASE
from recovery_runtime.ui_helpers import LOG_MENU_RETURN, render_log_menu, verify_phrase


def _disk() -> DiskMetadata:
    return DiskMetadata(
        disk_guid="{11111111-1111-1111-1111-111111111111}",
        disk_model="TestDisk",
        disk_serial="SN123",
        disk_size=1_000_000_000_000,
        windows_partition_uuid="{22222222-2222-2222-2222-222222222222}",
        efi_partition_uuid="{33333333-3333-3333-3333-333333333333}",
    )


def _populate_valid_backup(root: Path, *, backup_type: str = "standard") -> None:
    files = {
        "metadata/gpt_backup.bin": b"gpt",
        "images/efi_backup.pcl": b"efi",
        "images/windows_backup.pcl": b"win",
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    finalize_backup_manifest(
        ManifestContext(recovery_root=root, disk=_disk(), backup_type=backup_type)
    )


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
        backup_reason="valid backup already exists",
        restore_executable=False,
        restore_reason="rollback_required",
        delete_executable=True,
    )
    msg = actions_mod.run_backup_action(ctx, input_func=lambda _: "y")
    assert "disabled" in msg.lower() or "unavailable" in msg.lower()


def test_validate_restore_fail_disables_restore():
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
                                            validation={
                                                "allowed": False,
                                                "status": "REJECTED",
                                                "reason": "hash mismatch; restore forbidden",
                                                "checks": {},
                                            },
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
                    with patch.object(
                        ctx_mod,
                        "build_restore_plan",
                        return_value=MagicMock(
                            restore_allowed=False,
                            reason="incomplete",
                            validation={
                                "allowed": False,
                                "status": "REJECTED",
                                "reason": "incomplete",
                                "checks": {},
                            },
                        ),
                    ):
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
    assert "disabled" in msg.lower() or "unavailable" in msg.lower()


def test_backup_cancel_blocks_backup_admin():
    ctx = _ctx_with_menu(backup_executable=True, restore_executable=False)
    with patch.object(actions_mod, "create_backup_plan", return_value=MagicMock(status="PLANNED", reason=None, can_backup=True)):
        with patch.object(
            actions_mod,
            "plan_backup_run",
            side_effect=[
                MagicMock(
                    estimated_required_bytes=1024,
                    estimated_required_gb=0.0,
                    estimated_used_bytes=512,
                    estimation_method="df_used_space",
                    estimation_warning=None,
                    reason=None,
                    status="PLANNED",
                    can_backup=True,
                    recovery_image_free_bytes=4096,
                ),
            ],
            ):
                with patch.object(actions_mod, "_run_backup_admin_json_streaming") as mock_admin:
                    answers = iter(["n"])
                    msg = actions_mod.run_backup_action(
                        ctx,
                        input_func=lambda _prompt: next(answers),
                    )
    mock_admin.assert_not_called()
    assert "canceled" in msg.lower()


def test_restore_cancel_blocks_executor():
    ctx = _ctx_with_menu(restore_executable=True)
    ctx.recovery_root = Path("/tmp/r")
    ctx.current_disk = _disk()
    ctx.refresh = MagicMock()
    ctx.restore_plan = MagicMock(restore_allowed=True, compatible_restore=False)
    ctx.validation_result = MagicMock(allowed=True, reason=None)
    ctx.efi_rollback_available = True
    ctx.status_lines = []
    with patch.object(actions_mod, "authorize_restore_execution") as mock_auth:
        answers = iter(["n"])
        msg = actions_mod.run_restore_action(
            ctx,
            input_func=lambda _prompt: next(answers),
        )
    mock_auth.assert_not_called()
    assert "canceled" in msg.lower()


def test_restore_plan_summary_formats_nested_metadata(capsys):
    plan = SimpleNamespace(
        restore_allowed=True,
        target_disk={
            "disk_path": "/dev/nvme0n1",
            "metadata": {
                "disk_guid": "{11111111-1111-1111-1111-111111111111}",
                "disk_model": "TestDisk",
                "disk_serial": "SN123",
                "disk_size": 1_000_000_000_000,
                "windows_partition_uuid": "{22222222-2222-2222-2222-222222222222}",
                "efi_partition_uuid": "{33333333-3333-3333-3333-333333333333}",
            },
        },
        target_partitions={
            "efi": {"path": "/dev/nvme0n1p1", "fstype": "vfat", "label": "SYSTEM"},
            "windows": {"path": "/dev/nvme0n1p2", "fstype": "ntfs", "label": "Windows"},
        },
    )

    actions_mod._print_restore_plan_summary(plan, efi_rollback_available=False)

    out = capsys.readouterr().out
    assert "Target disk" in out
    assert "Target partitions" in out
    assert "disk_path" in out
    assert "metadata:" not in out
    assert '{"disk_guid"' not in out


def test_admin_backup_information_defaults_to_standard_backup(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root)
        ctx = RuntimeContext(recovery_root=root)
        actions_mod._print_admin_backup_information(ctx, SimpleNamespace(recovery_image={}))

    out = capsys.readouterr().out
    assert "Backup information" in out
    assert "standard backup" in out


def test_render_menu_user_mode_hides_admin_items(capsys):
    ctx = RuntimeContext(runtime_state=MagicMock(summary_lines=lambda: []))
    menu_mod.render_menu(ctx, admin_mode=False)

    out = capsys.readouterr().out
    assert "1. System Status" in out
    assert "2. Create Recovery Backup" in out
    assert "3. Restore System" in out
    assert "4. Reboot to Windows" in out
    assert "Delete Backup" not in out
    assert "Diagnostics Logs" not in out
    assert "Create Admin Backup" not in out
    assert "Create Compact Backup" not in out


def test_render_menu_admin_mode_shows_admin_items(capsys):
    ctx = RuntimeContext(runtime_state=MagicMock(summary_lines=lambda: []))
    menu_mod.render_menu(ctx, admin_mode=True)

    out = capsys.readouterr().out
    assert "Mode: Administrator" in out
    assert "4. Delete Backup" in out
    assert "5. Diagnostics Logs" in out
    assert "6. Restore Partition Preparation" in out
    assert "7. Clear Restore Failure Lock" in out
    assert "8. Boot/Recovery Status" in out
    assert "9. Reboot to Windows" in out
    assert "0. Return to Standard Mode" in out
    assert "Create Compact Backup" not in out
    assert out.index("9. Reboot to Windows") < out.index("0. Return to Standard Mode")


def test_admin_hotkey_enables_admin_menu():
    ctx = RuntimeContext(runtime_state=MagicMock(summary_lines=lambda: []))
    ctx.refresh = MagicMock()
    rendered_modes = []

    def _record_render(_ctx, *, admin_mode=False):
        rendered_modes.append(admin_mode)

    with patch.object(menu_mod, "render_menu", side_effect=_record_render):
        with patch.object(menu_mod, "read_choice", side_effect=[menu_mod.ADMIN_MODE_CHOICE, "9"]):
            with patch.object(
                menu_mod,
                "reboot_to_windows_action",
                return_value="Rebooting to Windows.",
            ):
                code = menu_mod.run_menu_loop(ctx, input_func=lambda _: "1")

    assert code == 0
    assert True in rendered_modes


def test_admin_menu_zero_returns_to_user_mode():
    ctx = RuntimeContext(runtime_state=MagicMock(summary_lines=lambda: []))
    ctx.refresh = MagicMock()
    rendered_modes = []

    def _record_render(_ctx, *, admin_mode=False):
        rendered_modes.append(admin_mode)

    with patch.object(menu_mod, "render_menu", side_effect=_record_render):
        with patch.object(
            menu_mod,
            "read_choice",
            side_effect=[menu_mod.ADMIN_MODE_CHOICE, "0", "4"],
        ):
            with patch.object(
                menu_mod,
                "reboot_to_windows_action",
                return_value="Rebooting to Windows.",
            ):
                code = menu_mod.run_menu_loop(ctx, input_func=lambda _: "1")

    assert code == 0
    assert True in rendered_modes
    admin_index = rendered_modes.index(True)
    assert False in rendered_modes[admin_index + 1 :]


def test_boot_recovery_status_action_formats_admin_status():
    ctx = RuntimeContext(
        recovery_root=Path("/mnt/recovery"),
        runtime_state=MagicMock(summary_lines=lambda: []),
    )
    ctx.refresh = MagicMock()
    ctx.status_lines = [
        "Recovery Image: OK",
        "Recovery Linux: OK",
        "Windows Boot Mgr: OK",
        "BootOrder: Recoverix Boot Manager first",
    ]
    ctx.firmware_state = None
    msg = actions_mod.boot_recovery_status_action(ctx)

    assert "Boot/Recovery Status" in msg
    assert "Recovery status" in msg
    assert "Boot status" in msg


def test_render_menu_admin_mode_marks_unsupported_backup(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root, backup_type="admin_compact")
        ctx = RuntimeContext(
            recovery_root=root,
            valid_backup=True,
            runtime_state=MagicMock(summary_lines=lambda: []),
            menu=MenuAvailability(
                backup_executable=False,
                backup_reason="Unsupported backup image detected. Delete this backup image and create a new standard backup.",
                restore_executable=False,
                restore_reason="Unsupported backup image detected. Delete this backup image and create a new standard backup.",
                delete_executable=True,
            ),
        )
        menu_mod.render_menu(ctx, admin_mode=True)

    out = capsys.readouterr().out
    assert "2. Create Recovery Backup [disabled: Unsupported backup image detected." in out
    assert "3. Restore System [disabled: Unsupported backup image detected." in out
    assert "6. Restore Partition Preparation" in out
    assert "7. Clear Restore Failure Lock" in out
    assert "8. Boot/Recovery Status" in out
    assert "Create Compact Backup" not in out


def test_render_menu_user_mode_marks_unsupported_backup(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root, backup_type="admin_compact")
        ctx = RuntimeContext(
            recovery_root=root,
            valid_backup=True,
            runtime_state=MagicMock(summary_lines=lambda: []),
            menu=MenuAvailability(
                backup_executable=False,
                backup_reason="Unsupported backup image detected. Delete this backup image and create a new standard backup.",
                restore_executable=False,
                restore_reason="Unsupported backup image detected. Delete this backup image and create a new standard backup.",
            ),
        )
        menu_mod.render_menu(ctx, admin_mode=False)

    out = capsys.readouterr().out
    assert "2. Create Recovery Backup [disabled: Unsupported backup image detected." in out
    assert "3. Restore System [disabled: Unsupported backup image detected." in out


@patch.object(actions_mod, "_run_restore_admin_json_streaming")
@patch.object(actions_mod, "helper_available", return_value=True)
def test_restore_action_uses_runtime_admin_helper(_mock_helper, mock_admin):
    def _streaming(*args, progress_callback=None):
        assert args == ("run-restore", "--phrase", RESTORE_CONFIRMATION_PHRASE)
        assert progress_callback is not None
        progress_callback(
            {
                "stage": "windows_partclone_restore",
                "percent": 50,
                "message": "Restoring Windows partition",
            }
        )
        return 0, {"status": "COMPLETED", "result": {"success": True, "status": "COMPLETED"}}, ""

    mock_admin.side_effect = _streaming
    ctx = _ctx_with_menu(restore_executable=True)
    ctx.recovery_root = Path("/tmp/r")
    ctx.current_disk = _disk()
    ctx.refresh = MagicMock()
    entered = {"value": False}

    class _Suppress:
        def __enter__(self):
            entered["value"] = True
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    with patch.object(actions_mod, "suppress_tty_input", return_value=_Suppress()):
        answers = iter(["y", "y"])
        msg = actions_mod.run_restore_action(
            ctx,
            input_func=lambda _prompt: next(answers),
        )

    assert "completed" in msg.lower()
    assert entered["value"] is True
    mock_admin.assert_called_once()


def test_missing_logs_graceful():
    with tempfile.TemporaryDirectory() as tmp:
        msg = render_log_menu(Path(tmp), ["restore.log"], input_func=lambda _: "0")
    assert "no log files" in msg.lower()


def test_render_log_menu_supports_explicit_log_paths():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        external = root / "bootstrap.log"
        external.write_text("bootstrap line\n", encoding="utf-8")
        msg = render_log_menu(
            root,
            [("recovery-runtime-bootstrap.log", external)],
            input_func=lambda _: "0",
        )
    assert msg == LOG_MENU_RETURN


def test_menu_exception_does_not_exit_runtime():
    ctx = RuntimeContext(runtime_state=MagicMock(summary_lines=lambda: []))
    calls = {"n": 0}

    def flaky_refresh():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    ctx.refresh = flaky_refresh  # type: ignore[method-assign]
    with patch.object(menu_mod, "render_menu"):
        with patch.object(menu_mod, "read_choice", side_effect=["1", "", "4"]):
            with patch.object(menu_mod, "show_status_action", return_value="ok"):
                with patch.object(
                    menu_mod,
                    "reboot_to_windows_action",
                    return_value="Rebooting to Windows.",
                ):
                    code = menu_mod.run_menu_loop(ctx, input_func=lambda _: "1")
    assert code == 0


def test_runtime_context_uses_privileged_restore_check_when_available():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root)
        ctx = RuntimeContext(recovery_root=root)
        with patch("recovery_runtime.discover.discover_recovery_volumes") as mock_disc:
            mock_disc.return_value = (
                MagicMock(mountpoint=str(root), path="/dev/p5", label="RECOVERY_IMAGE", size=1),
                MagicMock(path="/dev/p6", label="RECOVERY_LINUX"),
                [],
            )
            layout = MagicMock(
                recovery_image=MagicMock(mountpoint=str(root), path="/dev/p5", label="RECOVERY_IMAGE"),
                disk_path="/dev/nvme0n1",
                efi=MagicMock(path="/dev/nvme0n1p1"),
                windows=MagicMock(path="/dev/nvme0n1p3"),
            )
            with patch.object(ctx_mod, "discover_layout", return_value=(None, layout)):
                with patch.object(ctx_mod, "build_disk_metadata", return_value=_disk()):
                    with patch.object(ctx_mod, "read_bitlocker_state", return_value="OFF"):
                        with patch.object(ctx_mod, "read_secure_boot_state", return_value="UNKNOWN"):
                            with patch.object(ctx_mod, "read_firmware_boot", return_value=MagicMock(boot_order=[])):
                                with patch.object(ctx_mod, "has_valid_recovery_backup", return_value=True):
                                    with patch.object(ctx_mod, "has_incomplete_backup", return_value=False):
                                        with patch.object(ctx_mod, "apply_persisted_recovery_state", return_value=MagicMock()):
                                            with patch.object(ctx_mod, "helper_available", return_value=True):
                                                with patch.object(
                                                    ctx_mod,
                                                    "run_runtime_admin_json",
                                                    return_value=(
                                                        0,
                                                        {
                                                            "status": "COMPLETED",
                                                            "validation": {
                                                                "allowed": True,
                                                                "status": "PASS",
                                                                "reason": None,
                                                                "checks": {},
                                                            },
                                                            "restore_plan": {
                                                                "status": "PLANNED",
                                                                "dry_run": True,
                                                                "simulation_only": True,
                                                                "execution_allowed": False,
                                                                "restore_allowed": True,
                                                                "restore_disabled": False,
                                                                "reason": None,
                                                                "failure_reasons": [],
                                                                "target_disk": {},
                                                                "target_partitions": {},
                                                                "recovery_image": {},
                                                                "validation": {
                                                                    "allowed": True,
                                                                    "status": "PASS",
                                                                    "reason": None,
                                                                    "checks": {},
                                                                },
                                                                "checks": [],
                                                                "planned_commands": {},
                                                                "planned_efi_operations": [],
                                                                "planned_gpt_rollback": {},
                                                                "planned_bootorder_repair": {},
                                                                "planned_operations": [],
                                                            },
                                                        },
                                                        "",
                                                    ),
                                                ):
                                                    with patch.object(
                                                        ctx_mod,
                                                        "verify_windows_boot_manager",
                                                        return_value=MagicMock(passed=True, status="PLANNED"),
                                                    ):
                                                        ctx.refresh()
        assert ctx.menu.restore_executable is True


@patch.object(actions_mod, "_run_backup_admin_json_streaming", return_value=(0, {"status": "COMPLETED"}, ""))
@patch.object(actions_mod, "_format_recovery_image_for_backup", return_value=Path("/mnt/recovery"))
@patch.object(actions_mod, "create_backup_plan")
@patch.object(actions_mod, "plan_backup_run")
def test_backup_action_success(mock_plan_run, mock_create, _mock_format, mock_admin):
    mock_create.return_value = MagicMock(status="PLANNED", reason=None, can_backup=True)
    mock_plan_run.side_effect = [
        MagicMock(
            estimated_required_bytes=1024,
            estimated_required_gb=0.0,
            estimated_used_bytes=512,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason=None,
            status="PLANNED",
            can_backup=True,
            recovery_image_free_bytes=4096,
        ),
        MagicMock(
            estimated_required_bytes=1024,
            estimated_required_gb=0.0,
            estimated_used_bytes=512,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason=None,
            status="PLANNED",
            can_backup=True,
            recovery_image_free_bytes=4096,
        ),
    ]
    ctx = _ctx_with_menu(backup_executable=True)
    ctx.refresh = MagicMock()

    answers = iter(["y", "y", "y"])
    msg = actions_mod.run_backup_action(ctx, input_func=lambda _prompt: next(answers))
    assert "completed" in msg.lower()
    mock_admin.assert_called_once()
    assert mock_admin.call_args.args == ("run-backup",)


@patch.object(actions_mod, "create_backup_plan")
@patch.object(actions_mod, "plan_backup_run")
def test_backup_action_blocks_on_insufficient_free_space(mock_plan_run, mock_create):
    mock_create.return_value = MagicMock(status="PLANNED", reason=None, can_backup=True)
    mock_plan_run.side_effect = [
        MagicMock(
            estimated_required_bytes=50 * 1024**3,
            estimated_required_gb=50.0,
            estimated_used_bytes=49 * 1024**3,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason="insufficient recovery image space",
            status="REJECTED",
            can_backup=False,
            recovery_image_free_bytes=10 * 1024**3,
        ),
        MagicMock(
            estimated_required_bytes=50 * 1024**3,
            estimated_required_gb=50.0,
            estimated_used_bytes=49 * 1024**3,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason="insufficient recovery image space",
            status="REJECTED",
            can_backup=False,
            recovery_image_free_bytes=10 * 1024**3,
        ),
    ]
    ctx = _ctx_with_menu(backup_executable=True)
    ctx.refresh = MagicMock()

    with patch.object(actions_mod, "_format_recovery_image_for_backup", return_value=Path("/mnt/recovery")):
        with patch.object(actions_mod, "_run_backup_admin_json_streaming") as mock_admin:
            answers = iter(["y", "y"])
            msg = actions_mod.run_backup_action(ctx, input_func=lambda _prompt: next(answers))

    mock_admin.assert_not_called()
    assert "insufficient" in msg.lower()
    assert "required" in msg.lower()
    assert "free" in msg.lower()


def test_backup_admin_json_decode_uses_last_json_line():
    payload = decode_json_payload("noise\n{\"status\":\"COMPLETED\"}\n")
    assert payload["status"] == "COMPLETED"


@patch.object(actions_mod, "_format_recovery_image_for_backup", return_value=Path("/mnt/recovery"))
@patch.object(actions_mod, "_run_backup_admin_json_streaming", return_value=(0, {"status": "COMPLETED"}, ""))
@patch.object(actions_mod, "create_backup_plan")
@patch.object(actions_mod, "plan_backup_run")
def test_backup_action_formats_then_runs_backup(
    mock_plan_run,
    mock_create,
    mock_admin,
    _mock_format,
):
    mock_create.return_value = MagicMock(status="PLANNED", reason=None, can_backup=True)
    mock_plan_run.side_effect = [
        MagicMock(
            estimated_required_bytes=50 * 1024**3,
            estimated_required_gb=50.0,
            estimated_used_bytes=49 * 1024**3,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason="insufficient recovery image space",
            status="REJECTED",
            can_backup=False,
            recovery_image_free_bytes=10 * 1024**3,
        ),
        MagicMock(
            estimated_required_bytes=50 * 1024**3,
            estimated_required_gb=50.0,
            estimated_used_bytes=49 * 1024**3,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason=None,
            status="PLANNED",
            can_backup=True,
            recovery_image_free_bytes=100 * 1024**3,
        ),
    ]
    ctx = _ctx_with_menu(backup_executable=True)
    ctx.refresh = MagicMock()

    answers = iter(["y", "y", "y"])
    msg = actions_mod.run_backup_action(ctx, input_func=lambda _prompt: next(answers))

    mock_admin.assert_called_once()
    assert mock_admin.call_args.args == ("run-backup",)
    assert "completed" in msg.lower()


@patch.object(actions_mod, "_format_recovery_image_for_backup", return_value=Path("/mnt/recovery"))
@patch.object(actions_mod, "create_backup_plan")
@patch.object(actions_mod, "plan_backup_run")
def test_backup_action_prints_progress_updates(mock_plan_run, mock_create, _mock_format, capsys):
    mock_create.return_value = MagicMock(status="PLANNED", reason=None, can_backup=True)
    mock_plan_run.side_effect = [
        MagicMock(
            estimated_required_bytes=1024,
            estimated_required_gb=0.0,
            estimated_used_bytes=512,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason=None,
            status="PLANNED",
            can_backup=True,
            recovery_image_free_bytes=4096,
        ),
        MagicMock(
            estimated_required_bytes=1024,
            estimated_required_gb=0.0,
            estimated_used_bytes=512,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason=None,
            status="PLANNED",
            can_backup=True,
            recovery_image_free_bytes=4096,
        ),
    ]
    ctx = _ctx_with_menu(backup_executable=True)
    ctx.refresh = MagicMock()

    def _streaming(*args, progress_callback=None):
        assert args == ("run-backup",)
        assert progress_callback is not None
        progress_callback(
            {
                "stage": "prepare",
                "step": 1,
                "total": 5,
                "message": "Preparing backup workspace...",
            }
        )
        progress_callback(
            {
                "stage": "windows_backup",
                "step": 2,
                "total": 5,
                "message": "Backing up Windows partition...",
            }
        )
        return 0, {"status": "COMPLETED"}, ""

    answers = iter(["y", "y", "y"])
    with patch.object(actions_mod, "_run_backup_admin_json_streaming", side_effect=_streaming):
        msg = actions_mod.run_backup_action(ctx, input_func=lambda _prompt: next(answers))

    out = capsys.readouterr().out
    assert "[1/5] Preparing backup workspace..." in out
    assert "[2/5] Backing up Windows partition..." in out
    assert "completed" in msg.lower()


@patch.object(actions_mod, "_format_recovery_image_for_backup", return_value=Path("/mnt/recovery"))
@patch.object(actions_mod, "_run_backup_admin_json_streaming", return_value=(0, {"status": "COMPLETED"}, ""))
@patch.object(actions_mod, "create_backup_plan")
@patch.object(actions_mod, "plan_backup_run")
def test_backup_action_suppresses_tty_input_during_run(
    mock_plan_run,
    mock_create,
    _mock_admin,
    _mock_format,
):
    mock_create.return_value = MagicMock(status="PLANNED", reason=None, can_backup=True)
    mock_plan_run.side_effect = [
        MagicMock(
            estimated_required_bytes=1024,
            estimated_required_gb=0.0,
            estimated_used_bytes=512,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason=None,
            status="PLANNED",
            can_backup=True,
            recovery_image_free_bytes=4096,
        ),
        MagicMock(
            estimated_required_bytes=1024,
            estimated_required_gb=0.0,
            estimated_used_bytes=512,
            estimation_method="df_used_space",
            estimation_warning=None,
            reason=None,
            status="PLANNED",
            can_backup=True,
            recovery_image_free_bytes=4096,
        ),
    ]
    ctx = _ctx_with_menu(backup_executable=True)
    ctx.refresh = MagicMock()
    entered = {"value": False}

    class _Suppress:
        def __enter__(self):
            entered["value"] = True
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    answers = iter(["y", "y", "y"])
    with patch.object(actions_mod, "suppress_tty_input", return_value=_Suppress()):
        msg = actions_mod.run_backup_action(ctx, input_func=lambda _prompt: next(answers))

    assert entered["value"] is True
    assert "completed" in msg.lower()


def test_delete_cancel_blocks_delete():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root)
        ctx = _ctx_with_menu(delete_executable=True)
        ctx.recovery_root = root
        answers = iter(["y", "n"])
        msg = actions_mod.delete_backup_action(
            ctx,
            input_func=lambda _prompt: next(answers),
        )
    assert "canceled" in msg.lower()


@patch.object(actions_mod, "run_runtime_admin_json")
@patch.object(actions_mod, "helper_available", return_value=True)
def test_delete_action_uses_runtime_admin_helper(_mock_helper, mock_admin):
    mock_admin.return_value = (
        0,
        {
            "status": "COMPLETED",
            "message": "Backup data deleted (NO_VALID_RECOVERY_IMAGE).",
        },
        "",
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _populate_valid_backup(root)
        ctx = _ctx_with_menu(delete_executable=True)
        ctx.recovery_root = root
        ctx.refresh = MagicMock()
        answers = iter(["y", "y"])
        msg = actions_mod.delete_backup_action(
            ctx,
            input_func=lambda _prompt: next(answers),
        )
    assert "deleted" in msg.lower()
    mock_admin.assert_called_once_with("delete-backup")


@patch.object(actions_mod, "run_runtime_admin_json")
@patch.object(actions_mod, "helper_available", return_value=True)
def test_clear_restore_failure_lock_action_uses_runtime_admin_helper(_mock_helper, mock_admin):
    mock_admin.return_value = (
        0,
        {
            "status": "COMPLETED",
            "message": "Restore failure lock cleared.",
            "backup_preserved": True,
        },
        "",
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "state").mkdir()
        (root / "state" / "recovery_state.json").write_text(
            json.dumps(
                {
                    "rollback_required": True,
                    "restore_in_progress": False,
                    "current_stage": "windows_partclone_restore",
                    "last_failure_reason": "partclone size check failed",
                }
            ),
            encoding="utf-8",
        )
        ctx = _ctx_with_menu()
        ctx.recovery_root = root
        ctx.refresh = MagicMock()
        msg = actions_mod.clear_restore_failure_lock_action(
            ctx,
            input_func=lambda _prompt: "y",
        )

    assert "lock cleared" in msg.lower()
    assert "not deleted" in msg.lower()
    mock_admin.assert_called_once_with("clear-restore-failure-lock")


@patch.object(actions_mod, "run_runtime_admin_json")
@patch.object(actions_mod, "helper_available", return_value=True)
def test_reboot_to_windows_action_uses_runtime_admin_helper(_mock_helper, mock_admin):
    mock_admin.return_value = (
        0,
        {
            "status": "COMPLETED",
            "message": "BootNext set to Windows Boot Manager; rebooting.",
            "windows_boot_id": "0000",
        },
        "",
    )
    ctx = _ctx_with_menu()
    msg = actions_mod.reboot_to_windows_action(ctx, input_func=lambda _prompt: "y")
    assert "Windows Boot Manager" in msg
    mock_admin.assert_called_once_with("reboot-windows")


@patch.object(actions_mod, "run_runtime_admin_json")
@patch.object(actions_mod, "helper_available", return_value=True)
def test_reboot_to_windows_action_cancel_does_not_call_helper(_mock_helper, mock_admin):
    ctx = _ctx_with_menu()
    msg = actions_mod.reboot_to_windows_action(ctx, input_func=lambda _prompt: "n")
    assert "canceled" in msg.lower()
    mock_admin.assert_not_called()


def test_find_windows_boot_id_from_efibootmgr_output():
    output = "\n".join(
        [
            "BootCurrent: 0001",
            "BootOrder: 0001,0002,0000",
            "Boot0000* Windows Boot Manager\tHD(1,GPT,...)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)",
            "Boot0001* RecoveryBoot\tHD(1,GPT,...)/File(\\EFI\\RecoveryBoot\\shimx64.efi)",
        ]
    )
    assert backup_admin_mod._find_windows_boot_id(output) == "0000"


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

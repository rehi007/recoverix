"""Tests for the privileged backup helper dispatcher."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import recovery_runtime.backup_admin as backup_admin
from recovery_runtime.discover import DiscoveredVolume


def test_systemd_run_command_sets_child_environment():
    command = backup_admin._systemd_run_command(["reset-recovery-image"])

    assert command[0] == backup_admin.SYSTEMD_RUN_PATH
    assert "--wait" in command
    assert "--pipe" in command
    assert "--collect" in command
    assert "--property=StandardInput=null" in command
    assert (
        f"--setenv={backup_admin.SYSTEMD_RUN_CHILD_ENV}=1" in command
    )
    assert f"--setenv=PYTHONPATH={backup_admin.RECOVERIX_PYTHONPATH}" in command
    assert command[-1] == "reset-recovery-image"


@patch.object(backup_admin.subprocess, "Popen")
def test_run_via_transient_root_service_forwards_streams(mock_popen, capsys):
    process = MagicMock()
    process.stdout = iter(
        [
            '__RECOVERIX_PROGRESS__ {"step":1,"total":5,"message":"Preparing..."}\n',
            '{"status":"FAILED","reason":"boom"}\n',
        ]
    )
    process.wait.return_value = 7
    mock_popen.return_value = process

    rc = backup_admin._run_via_transient_root_service(["run-backup"])

    captured = capsys.readouterr()
    assert rc == 7
    assert "__RECOVERIX_PROGRESS__" in captured.out
    assert '{"status":"FAILED","reason":"boom"}' in captured.out
    assert captured.err == ""


@patch.object(backup_admin, "_cmd_check_restore", return_value=0)
@patch.object(backup_admin, "_run_via_transient_root_service")
@patch.object(backup_admin, "_require_root")
def test_main_check_restore_skips_transient_dispatch(
    _mock_require_root,
    mock_dispatch,
    mock_check_restore,
):
    rc = backup_admin.main(["check-restore"])

    assert rc == 0
    mock_dispatch.assert_not_called()
    mock_check_restore.assert_called_once()


@patch.object(backup_admin, "_write_audit_log")
@patch.object(backup_admin, "run_command")
@patch.object(backup_admin, "_require_root")
@patch.object(backup_admin, "require_linux")
@patch.object(backup_admin, "discover_volumes")
def test_delete_backup_data_remounts_rw_and_clears_backup_tree(
    mock_discover_volumes,
    _mock_require_linux,
    _mock_require_root,
    mock_run_command,
    _mock_audit,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for relative in (
            "images/windows_backup.pcl",
            "metadata/gpt_backup.bin",
            "manifests/recovery-manifest.json",
            "hashes/manifest.sha256",
            "logs/restore.log",
            "state/incomplete_backup",
            "recovery-manifest.json",
            "recovery-manifest.sha256",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")

        mock_discover_volumes.return_value = [
            DiscoveredVolume(
                name="nvme0n1p4",
                path="/dev/nvme0n1p4",
                label="RECOVERY_IMAGE",
                fstype="ext4",
                size=1,
                mountpoint=str(root),
                device_type="part",
            )
        ]
        mock_run_command.return_value = MagicMock(returncode=0, stdout="", stderr="")

        payload = backup_admin.delete_backup_data()

        assert payload["status"] == "COMPLETED"
        assert (root / "images").exists() is False
        assert (root / "metadata").exists() is False
        assert (root / "manifests").exists() is False
        assert (root / "hashes").exists() is False
        assert (root / "recovery-manifest.json").exists() is False
        assert (root / "recovery-manifest.sha256").exists() is False
        state = json.loads((root / "state" / "recovery_state.json").read_text(encoding="utf-8"))
        assert state["current_stage"] == "NO_VALID_RECOVERY_IMAGE"

        commands = [call.args[0] for call in mock_run_command.call_args_list]
        assert ["mount", "-o", "remount,rw", "/dev/nvme0n1p4", str(root)] in commands
        assert ["mount", "-o", "remount,ro", "/dev/nvme0n1p4", str(root)] in commands


@patch.object(backup_admin, "RestoreExecutor")
@patch.object(backup_admin, "build_execution_context", return_value=MagicMock())
@patch.object(backup_admin, "authorize_restore_execution")
@patch.object(backup_admin, "run_command")
@patch.object(backup_admin, "_require_root")
@patch.object(backup_admin, "require_linux")
@patch.object(backup_admin, "discover_volumes")
def test_run_restore_apply_remounts_recovery_image_rw_then_ro(
    mock_discover_volumes,
    _mock_require_linux,
    _mock_require_root,
    mock_run_command,
    mock_authorize,
    mock_build_ctx,
    mock_executor_cls,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mock_discover_volumes.return_value = [
            DiscoveredVolume(
                name="nvme0n1p4",
                path="/dev/nvme0n1p4",
                label="RECOVERY_IMAGE",
                fstype="ext4",
                size=1,
                mountpoint=str(root),
                device_type="part",
            )
        ]
        mock_run_command.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_authorize.return_value = MagicMock(to_dict=lambda: {"allowed": True})
        mock_executor = MagicMock()
        mock_executor.execute.return_value = MagicMock(
            success=True,
            reason=None,
            to_dict=lambda: {"success": True},
        )
        mock_executor_cls.return_value = mock_executor

        payload = backup_admin.run_restore_apply("RESTORE THIS DEVICE")

        assert payload["status"] == "COMPLETED"
        mock_build_ctx.assert_called_once()
        mock_executor.execute.assert_called_once()
        commands = [call.args[0] for call in mock_run_command.call_args_list]
        assert commands[0] == ["mount", "-o", "remount,rw", "/dev/nvme0n1p4", str(root)]
        assert commands[-1] == ["mount", "-o", "remount,ro", "/dev/nvme0n1p4", str(root)]


@patch.object(backup_admin, "_require_root")
@patch.object(backup_admin, "require_linux")
def test_admin_backup_plan_is_disabled(_mock_require_linux, _mock_require_root):
    payload = backup_admin.run_admin_backup_plan()

    assert payload["status"] == "DISABLED"
    assert payload["can_create"] is False
    assert "no longer supported" in payload["reason"]


@patch.object(backup_admin, "_require_root")
@patch.object(backup_admin, "require_linux")
def test_admin_backup_apply_is_disabled(_mock_require_linux, _mock_require_root):
    payload = backup_admin.run_admin_backup_apply()

    assert payload["status"] == "DISABLED"
    assert "no longer supported" in payload["reason"]


@patch.object(backup_admin, "run_command")
@patch.object(backup_admin.subprocess, "Popen")
@patch.object(backup_admin, "require_linux")
def test_reboot_pc_does_not_set_bootnext(
    _mock_require_linux,
    mock_popen,
    mock_run_command,
):
    payload = backup_admin.reboot_pc()

    assert payload["status"] == "COMPLETED"
    mock_run_command.assert_not_called()
    mock_popen.assert_called_once_with(["systemctl", "reboot", "--no-wall"])

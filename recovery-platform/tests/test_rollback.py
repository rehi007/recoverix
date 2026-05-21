"""Tests for rollback failure handling and rollback modules."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from recovery_runtime.state import RuntimeState, apply_persisted_recovery_state
from restore_engine.restore_state import (
    RecoveryState,
    load_recovery_state,
    mark_restore_started,
    save_recovery_state,
)
from rollback import failure_counter as fc
from rollback.efi_rollback import rollback_efi
from rollback.gpt_rollback import resolve_gpt_backup_file, rollback_gpt


def test_record_recoveryboot_failure_increments_count():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        counter = fc.record_recoveryboot_failure(root, "boot failed")
        assert counter.recoveryboot_failure_count == 1
        state = load_recovery_state(root)
        assert state.recoveryboot_failure_count == 1
        assert state.rollback_required is True


def test_windows_first_after_three_recoveryboot_failures():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for _ in range(3):
            fc.record_recoveryboot_failure(root, "failed")
        state = load_recovery_state(root)
        assert fc.should_apply_windows_first_policy(state) is True
        counter = fc.load_failure_counter_state(root)
        assert counter.windows_first_required is True
        assert counter.reboot_loop_risk is True


def test_windows_first_boot_order():
    order = fc.build_windows_first_boot_order(
        windows_boot_id="{windows}",
        recovery_boot_id="{recovery}",
        current_order=["{other}", "{recovery}", "{windows}"],
    )
    assert order[0] == "{windows}"
    assert order[1] == "{recovery}"


def test_bootnext_abuse_rejected():
    assert fc.validate_bootnext_policy(
        boot_next_policy="preserve",
        planned_commands=['bcdedit /set {fwbootmgr} bootnext {recovery-id}'],
    ) is False
    assert fc.validate_bootnext_policy(
        boot_next_policy="preserve",
        planned_commands=['bcdedit /set {fwbootmgr} displayorder {windows} {recovery}'],
    ) is True


def test_restore_in_progress_detection():
    state = RecoveryState(restore_in_progress=True, last_restore_started_at="2026-01-01T00:00:00+00:00")
    from restore_engine.restore_state import is_restore_in_progress, is_interrupted_restore

    assert is_restore_in_progress(state) is True
    assert is_interrupted_restore(state, stale_seconds=1) is True


def test_interrupted_restore_disables_runtime_restore():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = RecoveryState(
            restore_in_progress=True,
            last_restore_started_at="2020-01-01T00:00:00+00:00",
        )
        save_recovery_state(root, state)
        runtime = RuntimeState(recovery_image=MagicMock(mountpoint=str(root), path="/dev/nvme0n1p5", label="RECOVERY_IMAGE"))
        apply_persisted_recovery_state(runtime, root)
        assert runtime.interrupted_restore is True
        assert runtime.restore_enabled is False


def test_record_restore_failure_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mark_restore_started(root)
        fc.record_restore_failure_state(root, "partclone failed", stage="windows_restore")
        state = load_recovery_state(root)
        assert state.rollback_required is True
        assert state.restore_in_progress is False
        assert state.last_failure_reason == "partclone failed"
        assert state.last_restore_finished_at is not None
        assert fc.is_retry_allowed() is False
        assert fc.is_rollback_retry_allowed() is False


def test_efi_rollback_preserves_bootmgfw():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        snap = root / "pre_restore" / "efi"
        (snap / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
        (snap / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").write_bytes(b"orig")
        (snap / "EFI" / "RecoveryBoot").mkdir(parents=True)
        (snap / "EFI" / "RecoveryBoot" / "shimx64.efi").write_bytes(b"shim")

        esp = Path(tmp) / "esp"
        esp.mkdir()
        (esp / "EFI" / "Microsoft" / "Boot").mkdir(parents=True)
        (esp / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi").write_bytes(b"live")

        with patch("rollback.efi_rollback._find_efi_mount", return_value=esp):
            result = rollback_efi(
                recovery_root=root,
                efi_partition="/dev/nvme0n1p1",
                confirmed=True,
            )
        assert result.success is True
        assert result.bootmgfw_preserved is True
        assert (esp / "EFI" / "RecoveryBoot" / "shimx64.efi").is_file()


def test_efi_rollback_missing_snapshot_fails():
    with tempfile.TemporaryDirectory() as tmp:
        result = rollback_efi(
            recovery_root=Path(tmp),
            efi_partition="/dev/nvme0n1p1",
            confirmed=True,
        )
    assert result.success is False


@patch("rollback.gpt_rollback.run_command")
def test_gpt_rollback_single_attempt_no_retry(mock_cmd):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backup = root / "pre_restore" / "gpt_live.bin"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"gpt")
        mock_cmd.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = rollback_gpt(
            recovery_root=root,
            disk_path="/dev/nvme0n1",
            confirmed=True,
        )
    assert result.success is True
    assert mock_cmd.call_count == 1


@patch("rollback.gpt_rollback.run_command")
def test_gpt_rollback_failure_no_auto_retry(mock_cmd):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backup = root / "metadata" / "gpt_backup.bin"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"gpt")
        mock_cmd.return_value = MagicMock(returncode=1, stdout="", stderr="fail")
        result = rollback_gpt(
            recovery_root=root,
            disk_path="/dev/nvme0n1",
            confirmed=True,
            prefer_live_snapshot=False,
        )
    assert result.success is False
    assert mock_cmd.call_count == 1


def test_resolve_gpt_backup_prefers_live_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        live = root / "pre_restore" / "gpt_live.bin"
        manifest = root / "metadata" / "gpt_backup.bin"
        live.parent.mkdir(parents=True)
        manifest.parent.mkdir(parents=True)
        live.write_bytes(b"live")
        manifest.write_bytes(b"manifest")
        resolved = resolve_gpt_backup_file(root)
    assert resolved == live


def test_recovery_state_json_fields():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state = RecoveryState(
            current_stage="idle",
            recoveryboot_failure_count=2,
            rollback_required=True,
            last_successful_boot="windows",
        )
        save_recovery_state(root, state)
        data = json.loads((root / "state" / "recovery_state.json").read_text())
        assert data["recoveryboot_failure_count"] == 2
        assert data["last_successful_boot"] == "windows"
        assert "restore_in_progress" in data

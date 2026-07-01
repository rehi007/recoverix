"""GTK backup success flow helpers (finalize check + summary dialog text)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backup_engine.backup_finalize import FinalizeCheckResult, run_backup_finalize_check
from backup_engine.manifest import DiskMetadata, ManifestContext
from backup_engine.backup_finalize import run_backup_finalize_transaction
from tests.test_backup_finalize import _disk, _write_artifacts


def test_post_backup_finalize_check_success():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_artifacts(root)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        run_backup_finalize_transaction(ctx)
        result = run_backup_finalize_check(root)
        assert result.finalize_ok is True
        assert result.valid_backup_exists is True
        assert result.incomplete_backup_present is False


def test_backup_success_summary_includes_finalize_fields():
    from recovery_runtime.gtk_ui.window import RecoveryWindow

    finalize = FinalizeCheckResult(
        finalize_ok=True,
        valid_backup_exists=True,
        manifest_exists=True,
        hashes_valid=True,
        incomplete_backup_present=False,
    )
    apply_result = SimpleNamespace(status="COMPLETED")
    buttons = SimpleNamespace(backup_sensitive=False, restore_sensitive=True)
    win = RecoveryWindow.__new__(RecoveryWindow)
    body = win._build_backup_success_summary(
        apply_result=apply_result,
        finalize=finalize,
        buttons=buttons,
    )
    assert "마무리 검증           : 통과" in body
    assert "매니페스트            : 생성됨" in body
    assert "해시 검증             : 통과" in body
    assert "백업 버튼             : 비활성화" in body
    assert "복원 버튼             : 활성화" in body


@patch("recovery_runtime.gtk_ui.window.effective_status_for_ui")
@patch("recovery_runtime.gtk_ui.window.run_backup_finalize_check")
def test_run_post_backup_finalize_check_uses_mount(mock_check, mock_status):
    from recovery_runtime.gtk_ui.image_status import ImageStatus, UIButtonState
    from recovery_runtime.gtk_ui.window import RecoveryWindow

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        status = ImageStatus(
            recovery_image_partition_found=True,
            device="/dev/mock",
            filesystem="ext4",
            mount_point=root,
            mounted=True,
            errors=[],
        )
        mock_status.return_value = (
            status,
            UIButtonState(
                backup_sensitive=False,
                restore_sensitive=False,
                delete_visible=False,
                delete_sensitive=False,
            ),
        )
        mock_check.return_value = FinalizeCheckResult(
            finalize_ok=False,
            valid_backup_exists=False,
            manifest_exists=False,
            hashes_valid=False,
            incomplete_backup_present=True,
            errors=["missing manifest"],
        )
        win = RecoveryWindow.__new__(RecoveryWindow)
        win._admin_mode = False
        win._log = lambda _msg: None
        result = win._run_post_backup_finalize_check()
        mock_check.assert_called_once_with(root)
        assert result.finalize_ok is False
        assert "missing manifest" in result.errors


def test_reboot_buttons_use_separate_targets():
    from recovery_runtime.gtk_ui.window import RecoveryWindow

    win = RecoveryWindow.__new__(RecoveryWindow)
    exit_button = object()
    windows_button = object()
    calls: list[str] = []
    win._exit_btn = exit_button
    win._request_pc_reboot = lambda: calls.append("pc")
    win._request_reboot_to_windows = lambda **kwargs: calls.append(kwargs["title"])

    win._on_reboot_clicked(exit_button)
    win._on_reboot_clicked(windows_button)

    assert calls == ["pc", "Windows로 재부팅"]

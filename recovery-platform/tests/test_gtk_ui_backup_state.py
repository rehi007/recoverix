"""Tests for GTK Recovery UI mock backup state (no GTK required)."""

from __future__ import annotations

from pathlib import Path

from recovery_runtime.gtk_ui.backup_state import (
    compute_button_state,
    valid_backup_exists,
)


def test_valid_backup_marker_missing(tmp_path: Path):
    marker = tmp_path / "recoverix-valid-backup"
    assert valid_backup_exists(marker) is False


def test_valid_backup_marker_present(tmp_path: Path):
    marker = tmp_path / "recoverix-valid-backup"
    marker.write_text("mock\n", encoding="utf-8")
    assert valid_backup_exists(marker) is True


def test_button_state_without_valid_backup():
    state = compute_button_state(valid_backup=False, admin_mode=False)
    assert state.backup_sensitive is True
    assert state.restore_sensitive is False
    assert state.delete_visible is False


def test_button_state_with_valid_backup():
    state = compute_button_state(valid_backup=True, admin_mode=False)
    assert state.backup_sensitive is False
    assert state.restore_sensitive is True
    assert state.delete_visible is False


def test_button_state_admin_shows_delete_without_backup():
    state = compute_button_state(valid_backup=False, admin_mode=True)
    assert state.backup_sensitive is True
    assert state.restore_sensitive is False
    assert state.delete_visible is True


def test_button_state_admin_shows_delete_with_backup():
    state = compute_button_state(valid_backup=True, admin_mode=True)
    assert state.backup_sensitive is False
    assert state.restore_sensitive is True
    assert state.delete_visible is True

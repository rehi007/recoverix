"""Tests for legacy backup type compatibility helpers."""

from unittest.mock import patch

from backup_engine import admin_backup


def test_normalize_backup_type_maps_legacy_compact_names():
    assert admin_backup.normalize_backup_type("admin") == "admin_compact"
    assert admin_backup.normalize_backup_type("compact-admin") == "admin_compact"
    assert admin_backup.normalize_backup_type("compact backup") == "admin_compact"


def test_backup_type_label_keeps_legacy_compact_visible():
    assert admin_backup.backup_type_label("admin_compact") == "compact backup"
    assert admin_backup.backup_type_label("standard") == "standard backup"


@patch.object(admin_backup, "load_recovery_manifest", return_value={"backup_type": "admin_compact"})
@patch.object(admin_backup, "has_valid_backup", return_value=True)
def test_current_backup_type_reads_valid_manifest(_mock_has_valid_backup, _mock_manifest, tmp_path):
    assert admin_backup.current_backup_type(tmp_path) == "admin_compact"

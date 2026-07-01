"""Tests for EFI backup reliability helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from backup_engine.efi_backup import (
    EfiBackupError,
    prepare_efi_backup_output,
    run_efi_backup_precheck,
    verify_efi_artifact,
    verify_efi_source,
)
from backup_engine.backup_state import DEFAULT_IMAGE_FILES


def test_prepare_removes_empty_legacy_efi_backup_dir():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        legacy = root / "efi_backup"
        legacy.mkdir()
        output = prepare_efi_backup_output(root)
        assert output == root / DEFAULT_IMAGE_FILES["efi"]
        assert not legacy.exists()


def test_prepare_fails_when_legacy_dir_has_content():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        legacy = root / "efi_backup"
        legacy.mkdir()
        (legacy / "stale.txt").write_text("x", encoding="utf-8")
        with pytest.raises(EfiBackupError, match="legacy efi_backup"):
            prepare_efi_backup_output(root)


def test_verify_efi_artifact_requires_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with pytest.raises(EfiBackupError, match="missing"):
            verify_efi_artifact(root)


def test_verify_efi_artifact_rejects_tiny_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / DEFAULT_IMAGE_FILES["efi"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        with pytest.raises(EfiBackupError, match="too small"):
            verify_efi_artifact(root, min_size=512)


def test_verify_efi_artifact_accepts_valid_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / DEFAULT_IMAGE_FILES["efi"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1024)
        verify_efi_artifact(root)


@patch("backup_engine.efi_backup.run_readonly")
def test_verify_efi_source_accepts_vfat(mock_readonly):
    mock_readonly.return_value.returncode = 0
    mock_readonly.return_value.stdout = "vfat\n"
    with tempfile.TemporaryDirectory() as tmp:
        dev = Path(tmp) / "efi0"
        dev.touch()
        verify_efi_source(str(dev))


@patch("backup_engine.efi_backup.run_readonly")
def test_verify_efi_source_rejects_wrong_fstype(mock_readonly):
    mock_readonly.return_value.returncode = 0
    mock_readonly.return_value.stdout = "ext4\n"
    with tempfile.TemporaryDirectory() as tmp:
        dev = Path(tmp) / "efi0"
        dev.touch()
        with pytest.raises(EfiBackupError, match="unexpected filesystem"):
            verify_efi_source(str(dev))


@patch("backup_engine.efi_backup.verify_efi_source")
def test_run_efi_backup_precheck_returns_output(mock_verify):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        out = run_efi_backup_precheck("/dev/sda1", root)
        assert out == root / DEFAULT_IMAGE_FILES["efi"]
        mock_verify.assert_called_once_with("/dev/sda1")

"""Tests for canonical backup finalize transaction and CLI check."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from backup_engine.backup_finalize import (
    CANONICAL_MANIFEST_REL,
    normalize_runtime_backup_permissions,
    run_backup_finalize_check,
    run_backup_finalize_transaction,
    validate_finalize_state,
)
from backup_engine.backup_state import has_incomplete_backup, mark_incomplete_backup
from backup_engine.manifest import DiskMetadata, ManifestContext
from backup_engine.hash import sha256_file
from recovery_runtime.gtk_ui.image_status import _evaluate_backup_tree, derive_ui_button_state, ImageStatus


def _disk() -> DiskMetadata:
    return DiskMetadata(
        disk_guid="{11111111-1111-1111-1111-111111111111}",
        disk_model="TestDisk",
        disk_serial="SN123",
        disk_size=1_000_000_000_000,
        windows_partition_uuid="{22222222-2222-2222-2222-222222222222}",
        efi_partition_uuid="{33333333-3333-3333-3333-333333333333}",
    )


def _write_artifacts(root: Path, *, include_efi: bool = True) -> dict[str, str]:
    files = {
        "metadata/gpt_backup.bin": b"gpt-data",
        "images/windows_backup.pcl": b"windows-image",
    }
    if include_efi:
        files["images/efi_backup.pcl"] = b"efi-image"
    hashes: dict[str, str] = {}
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        hashes[relative] = sha256_file(path)
    return hashes


def test_incomplete_backup_blocks_finalize_ok():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_artifacts(root)
        mark_incomplete_backup(root, "backup in progress")
        check = validate_finalize_state(root)
        assert check.finalize_ok is False
        assert check.incomplete_backup_present is True
        assert check.valid_backup_exists is False


def test_missing_manifest_fails_finalize():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_artifacts(root)
        check = validate_finalize_state(root)
        assert check.finalize_ok is False
        assert check.manifest_exists is False
        assert any("manifest" in e for e in check.errors)


def test_efi_backup_failure_detected():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_artifacts(root, include_efi=False)
        (root / "efi_backup").mkdir()
        check = validate_finalize_state(root)
        assert check.finalize_ok is False
        assert check.efi_image_exists is False
        assert any("efi_backup.pcl" in e for e in check.errors)


def test_finalize_success_clears_incomplete_marker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        mark_incomplete_backup(root, "backup started")
        _write_artifacts(root)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        manifest, manifest_hash, path = run_backup_finalize_transaction(ctx)
        assert manifest["backup_complete"] is True
        assert (root / CANONICAL_MANIFEST_REL).is_file()
        assert not has_incomplete_backup(root)
        assert manifest_hash
        assert path.is_file()


def test_finalize_check_json_success_shape():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        _write_artifacts(root)
        run_backup_finalize_transaction(ctx)
        result = run_backup_finalize_check(root)
        data = result.to_json_dict()
        assert data["finalize_ok"] is True
        assert data["valid_backup_exists"] is True
        assert data["manifest_exists"] is True
        assert data["hashes_valid"] is True
        assert data["incomplete_backup_present"] is False
        assert data["errors"] == []


def test_gtk_ui_restore_enabled_after_finalize():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        _write_artifacts(root)
        run_backup_finalize_transaction(ctx)
        manifest, system_img, esp_img, hashes, valid = _evaluate_backup_tree(root)
        assert valid is True
        status = ImageStatus(
            recovery_image_partition_found=True,
            device="/dev/mock",
            filesystem="ext4",
            mount_point=root,
            mounted=True,
            manifest_exists=manifest,
            system_image_exists=system_img,
            esp_image_exists=esp_img,
            hashes_exist=hashes,
            valid_backup_exists=valid,
            errors=[],
        )
        buttons = derive_ui_button_state(status, admin_mode=False)
        assert buttons.backup_sensitive is False
        assert buttons.restore_sensitive is True


def test_gtk_ui_restore_disabled_while_incomplete():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_artifacts(root)
        mark_incomplete_backup(root, "partial")
        manifest, system_img, esp_img, hashes, valid = _evaluate_backup_tree(root)
        assert valid is False
        status = ImageStatus(
            recovery_image_partition_found=True,
            device="/dev/mock",
            filesystem="ext4",
            mount_point=root,
            mounted=True,
            manifest_exists=manifest,
            system_image_exists=system_img,
            esp_image_exists=esp_img,
            hashes_exist=hashes,
            valid_backup_exists=valid,
            errors=[],
        )
        buttons = derive_ui_button_state(status, admin_mode=False)
        assert buttons.restore_sensitive is False
        assert buttons.backup_sensitive is True


def test_canonical_manifest_schema_fields():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        _write_artifacts(root)
        run_backup_finalize_transaction(ctx)
        manifest = json.loads((root / CANONICAL_MANIFEST_REL).read_text(encoding="utf-8"))
        assert manifest["version"] == 1
        assert manifest["windows_image"] == "images/windows_backup.pcl"
        assert manifest["efi_image"] == "images/efi_backup.pcl"
        assert manifest["hashes"]["manifest"] == "hashes/manifest.sha256"
        assert manifest["backup_complete"] is True


def test_normalize_runtime_backup_permissions_makes_files_readable():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sample = root / "images" / "windows_backup.pcl"
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.write_bytes(b"win")
        sample.chmod(0o600)
        sample.parent.chmod(0o700)

        normalize_runtime_backup_permissions(root)

        assert oct(sample.stat().st_mode & 0o777) == "0o644"
        assert oct(sample.parent.stat().st_mode & 0o777) == "0o755"

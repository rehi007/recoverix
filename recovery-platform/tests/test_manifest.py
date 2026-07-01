"""Tests for manifest, backup safety, and restore validation."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from backup_engine.backup_state import (
    has_incomplete_backup,
    mark_incomplete_backup,
)
from backup_engine.hash import sha256_file
from backup_engine.manifest import (
    DiskMetadata,
    HashGenerationError,
    ManifestContext,
    ManifestCreationError,
    build_manifest,
    compute_device_id,
    compute_manifest_hash,
    create_recovery_manifest,
    finalize_backup_manifest,
    load_recovery_manifest,
)
from validation.image_validation import (
    validate_restore,
    validate_restore_compatible,
    validate_restore_compatible_quick,
    validate_restore_quick,
)


def _disk() -> DiskMetadata:
    return DiskMetadata(
        disk_guid="{11111111-1111-1111-1111-111111111111}",
        disk_model="TestDisk",
        disk_serial="SN123",
        disk_size=1_000_000_000_000,
        windows_partition_uuid="{22222222-2222-2222-2222-222222222222}",
        efi_partition_uuid="{33333333-3333-3333-3333-333333333333}",
    )


def _setup_artifacts(root: Path, *, include_windows: bool = True) -> dict:
    files = {
        "metadata/gpt_backup.bin": b"gpt-data",
        "images/efi_backup.pcl": b"efi-image",
    }
    if include_windows:
        files["images/windows_backup.pcl"] = b"windows-image"
    hashes = {}
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        hashes[relative] = sha256_file(path)
    return hashes


def test_finalize_manifest_after_all_artifacts():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        manifest, manifest_hash, path = finalize_backup_manifest(ctx)
        assert path.is_file()
        assert (root / "manifests" / "recovery-manifest.json").is_file()
        assert manifest["backup_complete"] is True
        assert not has_incomplete_backup(root)
        assert (root / "hashes" / "manifest.sha256").read_text(encoding="utf-8").strip() == manifest_hash


def test_finalize_manifest_records_admin_backup_type():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        ctx = ManifestContext(
            recovery_root=root,
            disk=_disk(),
            backup_type="admin_compact",
            restore_baseline_bytes=40 * 1024**3,
            source_windows_partition_size_bytes=100 * 1024**3,
            admin_backup={"compact_windows_partition_size_bytes": 40 * 1024**3},
        )
        manifest, _manifest_hash, _path = finalize_backup_manifest(ctx)
        assert manifest["backup_type"] == "admin_compact"
        assert manifest["restore_baseline_bytes"] == 40 * 1024**3
        assert manifest["source_windows_partition_size_bytes"] == 100 * 1024**3
        assert manifest["admin_backup"]["compact_windows_partition_size_bytes"] == 40 * 1024**3


def test_partial_backup_refuses_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root, include_windows=False)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        try:
            finalize_backup_manifest(ctx)
        except ManifestCreationError as exc:
            assert (
                "partial" in str(exc).lower()
                or "missing" in str(exc).lower()
                or "incomplete" in str(exc).lower()
            )
        else:
            raise AssertionError("expected ManifestCreationError")
        assert has_incomplete_backup(root)
        assert not (root / "recovery-manifest.json").is_file()


def test_hash_failure_marks_incomplete_and_no_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        with patch(
            "backup_engine.backup_finalize.sha256_files",
            side_effect=OSError("hash failed"),
        ):
            try:
                finalize_backup_manifest(ctx)
            except HashGenerationError:
                pass
            else:
                raise AssertionError("expected HashGenerationError")
        assert has_incomplete_backup(root)
        assert not (root / "recovery-manifest.json").is_file()


def test_manifest_failure_marks_incomplete():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        ctx = ManifestContext(recovery_root=root, disk=_disk())
        with patch(
            "backup_engine.backup_finalize.write_canonical_manifest_files",
            side_effect=OSError("write failed"),
        ):
            try:
                finalize_backup_manifest(ctx)
            except ManifestCreationError:
                pass
            else:
                raise AssertionError("expected ManifestCreationError")
        assert has_incomplete_backup(root)


def test_build_manifest_requires_hashes():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = ManifestContext(recovery_root=Path(tmp), disk=_disk())
        try:
            build_manifest(ctx, sha256_hashes={})
        except ManifestCreationError:
            pass
        else:
            raise AssertionError("expected ManifestCreationError")


def test_validate_restore_pass():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        result = validate_restore(root, _disk())
        assert result.allowed is True
        assert result.status == "PASS"


def test_validate_restore_incomplete_marker():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        mark_incomplete_backup(root, "backup failed")
        result = validate_restore(root, _disk())
        assert result.allowed is False
        assert result.allowed is False
        assert result.status == "REJECTED"


def test_validate_restore_partial_without_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root, include_windows=False)
        result = validate_restore(root, _disk())
        assert result.allowed is False
        assert result.status == "REJECTED"


def test_validate_restore_hash_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        (root / "images/windows_backup.pcl").write_bytes(b"tampered")
        result = validate_restore(root, _disk())
        assert result.allowed is False


def test_validate_restore_quick_skips_large_file_hashes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        (root / "images/windows_backup.pcl").write_bytes(b"tampered")
        result = validate_restore_quick(root, _disk())
        assert result.allowed is True
        assert result.status == "PASS"
        assert result.checks["hashes"]["status"] == "SKIPPED"


def test_validate_restore_device_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        other = DiskMetadata(
            disk_guid="{99999999-9999-9999-9999-999999999999}",
            disk_model="Other",
            disk_serial="OTHER",
            disk_size=2,
            windows_partition_uuid="{22222222-2222-2222-2222-222222222222}",
            efi_partition_uuid="{33333333-3333-3333-3333-333333333333}",
        )
        result = validate_restore(root, other)
        assert result.allowed is False


def test_validate_restore_compatible_accepts_device_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        other = DiskMetadata(
            disk_guid="{99999999-9999-9999-9999-999999999999}",
            disk_model="Other",
            disk_serial="OTHER",
            disk_size=1_000_000_000_000,
            windows_partition_uuid="{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}",
            efi_partition_uuid="{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}",
        )
        result = validate_restore_compatible(root, other)
        assert result.allowed is True
        assert result.status == "COMPATIBLE"


def test_validate_restore_compatible_quick_still_skips_file_hashes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        (root / "images/windows_backup.pcl").write_bytes(b"tampered")
        other = DiskMetadata(
            disk_guid="{99999999-9999-9999-9999-999999999999}",
            disk_model="Other",
            disk_serial="OTHER",
            disk_size=1_000_000_000_000,
            windows_partition_uuid="{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}",
            efi_partition_uuid="{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}",
        )
        result = validate_restore_compatible_quick(root, other)
        assert result.allowed is True
        assert result.checks["hashes"]["status"] == "SKIPPED"


def test_validate_restore_fail_closed_on_exception():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        with patch(
            "validation.image_validation.load_recovery_manifest",
            side_effect=RuntimeError("boom"),
        ):
            result = validate_restore(root, _disk())
        assert result.allowed is False
        assert result.reason == "validation_exception"


def test_validate_restore_requires_manifest_hash_sidecar():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_artifacts(root)
        create_recovery_manifest(ManifestContext(recovery_root=root, disk=_disk()))
        (root / "recovery-manifest.sha256").unlink()
        (root / "hashes" / "manifest.sha256").unlink()
        result = validate_restore(root, _disk())
        assert result.allowed is False
        assert "manifest hash" in (result.reason or "").lower()


def test_validate_restore_manifest_missing():
    with tempfile.TemporaryDirectory() as tmp:
        result = validate_restore(Path(tmp), _disk())
        assert result.allowed is False

"""recovery-manifest.json generation and manifest hashing."""

from __future__ import annotations

import os
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backup_engine.backup_state import (
    DEFAULT_IMAGE_FILES,
    clear_incomplete_backup,
    has_incomplete_backup,
    list_missing_artifacts,
    mark_incomplete_backup,
)
from backup_engine.hash import sha256_file, sha256_files, sha256_text
from common.logger import get_logger

logger = get_logger(__name__)

MANIFEST_FILENAME = "recovery-manifest.json"
MANIFEST_HASH_FILENAME = "recovery-manifest.sha256"
IMAGE_VERSION = "1"
PLATFORM_VERSION = "0.1.0"


def development_mode_enabled() -> bool:
    """Development mode flag written into recovery-manifest.json (default: on)."""
    return os.environ.get("RECOVERIX_BACKUP_DEV", "1").strip() not in ("0", "false", "False", "no")


class HashGenerationError(Exception):
    """Raised when SHA256 generation fails; manifest must not be created."""


class ManifestCreationError(Exception):
    """Raised when manifest creation fails; backup is invalid."""


@dataclass(frozen=True)
class DiskMetadata:
    """Disk and partition identifiers stored in the manifest."""

    disk_guid: str
    disk_model: str
    disk_serial: str
    disk_size: int
    windows_partition_uuid: str
    efi_partition_uuid: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ManifestContext:
    """Inputs required to build a recovery manifest."""

    recovery_root: Path
    disk: DiskMetadata
    backup_type: str = "standard"
    restore_baseline_bytes: Optional[int] = None
    source_windows_partition_size_bytes: Optional[int] = None
    admin_backup: Dict[str, Any] = field(default_factory=dict)
    image_files: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_IMAGE_FILES))
    tool_versions: Dict[str, str] = field(
        default_factory=lambda: {
            "partclone": "unknown",
            "recovery_platform": PLATFORM_VERSION,
        }
    )

    def device_id(self) -> str:
        payload = "|".join(
            [
                self.disk.disk_guid,
                self.disk.disk_model,
                self.disk.disk_serial,
                str(self.disk.disk_size),
            ]
        )
        return sha256_text(payload)

    def artifact_paths(self) -> List[str]:
        return list(self.image_files.values())


def compute_device_id(disk: DiskMetadata) -> str:
    return ManifestContext(recovery_root=Path("."), disk=disk).device_id()


def build_manifest(
    ctx: ManifestContext,
    *,
    sha256_hashes: Dict[str, str],
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build recovery-manifest.json content.

    sha256_hashes must be precomputed after all artifacts exist.
    """
    if not sha256_hashes:
        raise ManifestCreationError("sha256_hashes required; refusing partial manifest")

    # Sidecar file naming convention for UI checks:
    #   images/<name>.pcl  -> hashes/<name>.sha256
    #   metadata/<name>.bin -> hashes/<name>.sha256
    sha256_filenames: Dict[str, str] = {}
    for rel in sha256_hashes.keys():
        sha256_filenames[rel] = str(Path("hashes") / f"{Path(rel).stem}.sha256")

    manifest: Dict[str, Any] = {
        "image_version": IMAGE_VERSION,
        "backup_version": IMAGE_VERSION,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "backup_type": ctx.backup_type,
        "disk_guid": ctx.disk.disk_guid,
        "disk_model": ctx.disk.disk_model,
        "disk_serial": ctx.disk.disk_serial,
        "disk_size": ctx.disk.disk_size,
        # Backward-compatible fields used by restore_engine/restore_planner.
        "windows_partition_uuid": ctx.disk.windows_partition_uuid,
        "efi_partition_uuid": ctx.disk.efi_partition_uuid,
        "source_partitions": {
            "windows_partition_uuid": ctx.disk.windows_partition_uuid,
            "efi_partition_uuid": ctx.disk.efi_partition_uuid,
        },
        "image_files": dict(ctx.image_files),
        "image_filenames": dict(ctx.image_files),
        "sha256_filenames": sha256_filenames,
        "sha256_hashes": dict(sha256_hashes),
        "tool_versions": dict(ctx.tool_versions),
        "device_id": ctx.device_id(),
        "backup_complete": True,
        "development_mode": development_mode_enabled(),
    }
    if ctx.restore_baseline_bytes is not None:
        manifest["restore_baseline_bytes"] = int(ctx.restore_baseline_bytes)
    if ctx.source_windows_partition_size_bytes is not None:
        manifest["source_windows_partition_size_bytes"] = int(
            ctx.source_windows_partition_size_bytes
        )
    if ctx.admin_backup:
        manifest["admin_backup"] = dict(ctx.admin_backup)
    return manifest


def canonical_manifest_json(manifest: Dict[str, Any]) -> str:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


def compute_manifest_hash(manifest: Dict[str, Any]) -> str:
    return sha256_text(canonical_manifest_json(manifest))


def write_recovery_manifest(
    recovery_root: Path,
    manifest: Dict[str, Any],
    *,
    write_hash_file: bool = True,
) -> Path:
    recovery_root.mkdir(parents=True, exist_ok=True)

    manifests_dir = recovery_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    # Canonical location: manifests/recovery-manifest.json
    manifest_canonical_path = manifests_dir / MANIFEST_FILENAME
    manifest_canonical_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("wrote manifest (canonical): %s", manifest_canonical_path)

    # Legacy location (root/): keep for backward compatibility.
    manifest_root_path = recovery_root / MANIFEST_FILENAME
    manifest_root_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("wrote manifest: %s", manifest_root_path)

    # Legacy compatibility copy for older runtime consumers.
    metadata_dir = recovery_root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    manifest_metadata_path = metadata_dir / MANIFEST_FILENAME
    manifest_metadata_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("wrote manifest (legacy metadata): %s", manifest_metadata_path)

    hashes_dir = recovery_root / "hashes"
    hashes_dir.mkdir(parents=True, exist_ok=True)

    if write_hash_file:
        manifest_hash = compute_manifest_hash(manifest)

        # Legacy hash sidecar: recovery-manifest.sha256 at root.
        hash_root_path = recovery_root / MANIFEST_HASH_FILENAME
        hash_root_path.write_text(manifest_hash + "\n", encoding="utf-8")
        logger.info("wrote manifest hash: %s", hash_root_path)

        # Canonical hash sidecar expected by GTK UI/image_status.
        hash_canonical_path = hashes_dir / "manifest.sha256"
        hash_canonical_path.write_text(manifest_hash + "\n", encoding="utf-8")
        logger.info("wrote manifest hash (canonical): %s", hash_canonical_path)

        # Per-image hash sidecars:
        #   hashes/<artifact_stem>.sha256
        sha256_hashes: Dict[str, str] = manifest.get("sha256_hashes") or {}
        for rel, digest in sha256_hashes.items():
            sidecar_path = hashes_dir / f"{Path(rel).stem}.sha256"
            sidecar_path.write_text(str(digest) + "\n", encoding="utf-8")

    return manifest_canonical_path


def load_recovery_manifest(recovery_root: Path) -> Dict[str, Any]:
    candidates = [
        recovery_root / "manifests" / MANIFEST_FILENAME,  # canonical
        recovery_root / "metadata" / MANIFEST_FILENAME,  # legacy
        recovery_root / MANIFEST_FILENAME,  # legacy root
    ]
    for manifest_path in candidates:
        if manifest_path.is_file():
            manifest: Dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))

            # Schema normalization for legacy backups:
            #   images/system.pcl -> images/windows_backup.pcl
            #   images/esp.pcl    -> images/efi_backup.pcl
            remap: dict[str, str] = {
                "images/system.pcl": "images/windows_backup.pcl",
                "images/esp.pcl": "images/efi_backup.pcl",
            }

            def _maybe_file(rel: str) -> bool:
                return (recovery_root / rel).is_file()

            # Remap image_files.* fields.
            img_files = manifest.get("image_files") or {}
            if isinstance(img_files, dict):
                if img_files.get("windows") in remap and _maybe_file(remap[img_files["windows"]]):
                    img_files["windows"] = remap[img_files["windows"]]
                if img_files.get("efi") in remap and _maybe_file(remap[img_files["efi"]]):
                    img_files["efi"] = remap[img_files["efi"]]
                manifest["image_files"] = img_files

            # Remap sha256_hashes keys.
            sha256_hashes = manifest.get("sha256_hashes")
            if isinstance(sha256_hashes, dict):
                updated: Dict[str, Any] = {}
                for rel, digest in sha256_hashes.items():
                    rel2 = remap.get(rel, rel)
                    if rel2 != rel and not _maybe_file(rel2):
                        rel2 = rel
                    updated[rel2] = digest
                manifest["sha256_hashes"] = updated

            # Remap image_filenames (optional; not required for validation).
            img_names = manifest.get("image_filenames")
            if isinstance(img_names, dict):
                if img_names.get("windows") in remap and _maybe_file(remap[img_names["windows"]]):
                    img_names["windows"] = remap[img_names["windows"]]
                if img_names.get("efi") in remap and _maybe_file(remap[img_names["efi"]]):
                    img_names["efi"] = remap[img_names["efi"]]
                manifest["image_filenames"] = img_names

            # Remap sha256_filenames keys if present (optional; not required).
            sha_names = manifest.get("sha256_filenames")
            if isinstance(sha_names, dict):
                updated_names: Dict[str, Any] = {}
                for rel, side in sha_names.items():
                    rel2 = remap.get(rel, rel)
                    updated_names[rel2] = side
                manifest["sha256_filenames"] = updated_names

            return manifest
    raise FileNotFoundError(f"manifest missing under: {[str(p) for p in candidates]}")


def load_manifest_hash(recovery_root: Path) -> Optional[str]:
    candidates = [
        recovery_root / MANIFEST_HASH_FILENAME,  # legacy
        recovery_root / "hashes" / "manifest.sha256",  # canonical
    ]
    for hash_path in candidates:
        if hash_path.is_file():
            return hash_path.read_text(encoding="utf-8").strip()
    return None


def _invalidate_backup_on_failure(
    recovery_root: Path,
    reason: str,
    *,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    mark_incomplete_backup(recovery_root, reason, details=details)
    manifest_path = recovery_root / MANIFEST_FILENAME
    hash_path = recovery_root / MANIFEST_HASH_FILENAME
    if manifest_path.is_file():
        manifest_path.unlink()
        logger.warning("removed invalid manifest after failure: %s", manifest_path)
    if hash_path.is_file():
        hash_path.unlink()

    # Canonical locations written by the schema normalization.
    for rel in (
        Path("manifests") / MANIFEST_FILENAME,
        Path("metadata") / MANIFEST_FILENAME,
    ):
        canonical_manifest_path = recovery_root / rel
        if canonical_manifest_path.is_file():
            canonical_manifest_path.unlink()
            logger.warning(
                "removed invalid canonical manifest after failure: %s",
                canonical_manifest_path,
            )
    canonical_manifest_hash_path = recovery_root / "hashes" / "manifest.sha256"
    if canonical_manifest_hash_path.is_file():
        canonical_manifest_hash_path.unlink()
        logger.warning(
            "removed invalid canonical manifest hash after failure: %s",
            canonical_manifest_hash_path,
        )


def finalize_backup_manifest(
    ctx: ManifestContext,
    progress_callback=None,
) -> tuple[Dict[str, Any], str, Path]:
    """
    Finalize backup: canonical transaction (hash, manifest, validate, clear marker).

    Policies:
    - All artifacts must exist before manifest creation
    - Hash failure → no manifest, incomplete marker
    - Manifest failure → backup invalid, incomplete marker
    - incomplete_backup cleared only after finalize validation passes
    """
    from backup_engine.backup_finalize import run_backup_finalize_transaction

    return run_backup_finalize_transaction(ctx, progress_callback=progress_callback)


def create_recovery_manifest(ctx: ManifestContext) -> tuple[Dict[str, Any], str, Path]:
    """Create manifest only after all backup artifacts are complete (final step)."""
    return finalize_backup_manifest(ctx)


def verify_manifest_hash(recovery_root: Path, manifest: Dict[str, Any]) -> bool:
    expected = load_manifest_hash(recovery_root)
    computed = compute_manifest_hash(manifest)
    if expected is None:
        return False
    return expected == computed

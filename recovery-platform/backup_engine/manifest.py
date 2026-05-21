"""recovery-manifest.json generation and manifest hashing."""

from __future__ import annotations

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

    manifest: Dict[str, Any] = {
        "image_version": IMAGE_VERSION,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "disk_guid": ctx.disk.disk_guid,
        "disk_model": ctx.disk.disk_model,
        "disk_serial": ctx.disk.disk_serial,
        "disk_size": ctx.disk.disk_size,
        "windows_partition_uuid": ctx.disk.windows_partition_uuid,
        "efi_partition_uuid": ctx.disk.efi_partition_uuid,
        "image_files": dict(ctx.image_files),
        "sha256_hashes": dict(sha256_hashes),
        "tool_versions": dict(ctx.tool_versions),
        "device_id": ctx.device_id(),
        "backup_complete": True,
    }
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
    manifest_path = recovery_root / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("wrote manifest: %s", manifest_path)

    if write_hash_file:
        manifest_hash = compute_manifest_hash(manifest)
        hash_path = recovery_root / MANIFEST_HASH_FILENAME
        hash_path.write_text(manifest_hash + "\n", encoding="utf-8")
        logger.info("wrote manifest hash: %s", hash_path)

    return manifest_path


def load_recovery_manifest(recovery_root: Path) -> Dict[str, Any]:
    manifest_path = recovery_root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest missing: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def load_manifest_hash(recovery_root: Path) -> Optional[str]:
    hash_path = recovery_root / MANIFEST_HASH_FILENAME
    if not hash_path.is_file():
        return None
    return hash_path.read_text(encoding="utf-8").strip()


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


def finalize_backup_manifest(
    ctx: ManifestContext,
) -> tuple[Dict[str, Any], str, Path]:
    """
    Finalize backup: hash artifacts, write manifest (last step only).

    Policies:
    - All artifacts must exist before manifest creation
    - Hash failure → no manifest, incomplete marker
    - Manifest failure → backup invalid, incomplete marker
    """
    if has_incomplete_backup(ctx.recovery_root):
        raise ManifestCreationError("incomplete backup marker present; manifest refused")

    missing = list_missing_artifacts(ctx.recovery_root, ctx.artifact_paths())
    if missing:
        _invalidate_backup_on_failure(
            ctx.recovery_root,
            "artifacts incomplete",
            details={"missing": missing},
        )
        raise ManifestCreationError(
            f"partial backup: artifacts missing {missing}; manifest refused"
        )

    try:
        hashes = sha256_files(ctx.recovery_root, ctx.artifact_paths())
    except (OSError, FileNotFoundError) as exc:
        _invalidate_backup_on_failure(
            ctx.recovery_root,
            "hash generation failed",
            details={"error": str(exc)},
        )
        raise HashGenerationError(f"hash generation failed: {exc}") from exc

    try:
        manifest = build_manifest(ctx, sha256_hashes=hashes)
        manifest_hash = compute_manifest_hash(manifest)
        path = write_recovery_manifest(ctx.recovery_root, manifest)
        clear_incomplete_backup(ctx.recovery_root)
    except (OSError, TypeError, ValueError, ManifestCreationError) as exc:
        _invalidate_backup_on_failure(
            ctx.recovery_root,
            "manifest creation failed",
            details={"error": str(exc)},
        )
        raise ManifestCreationError(f"manifest creation failed: {exc}") from exc

    return manifest, manifest_hash, path


def create_recovery_manifest(ctx: ManifestContext) -> tuple[Dict[str, Any], str, Path]:
    """Create manifest only after all backup artifacts are complete (final step)."""
    return finalize_backup_manifest(ctx)


def verify_manifest_hash(recovery_root: Path, manifest: Dict[str, Any]) -> bool:
    expected = load_manifest_hash(recovery_root)
    computed = compute_manifest_hash(manifest)
    if expected is None:
        return False
    return expected == computed

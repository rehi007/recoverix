"""Manifest and integrity verification (SHA256 stub)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from common.logger import get_logger

logger = get_logger(__name__)

MANIFEST_CANDIDATES = (
    "manifest.json",
    "recovery/manifest.json",
    "backup/manifest.json",
)


@dataclass(frozen=True)
class IntegrityReport:
    """Result of manifest / checksum inspection."""

    manifest_found: bool
    manifest_path: Optional[str]
    sha256_status: str
    message: str

    def to_dict(self) -> dict:
        return {
            "manifest_found": self.manifest_found,
            "manifest_path": self.manifest_path,
            "sha256_status": self.sha256_status,
            "message": self.message,
        }


def find_manifest(root: Path) -> Optional[Path]:
    """Search common manifest locations under a mount root."""
    for relative in MANIFEST_CANDIDATES:
        candidate = root / relative
        if candidate.is_file():
            logger.info("manifest found: %s", candidate)
            return candidate
    return None


def check_manifest(root: Path) -> IntegrityReport:
    """Verify whether a backup manifest exists on the mounted volume."""
    manifest = find_manifest(root)
    if manifest is None:
        return IntegrityReport(
            manifest_found=False,
            manifest_path=None,
            sha256_status="skipped",
            message="manifest not found",
        )
    return IntegrityReport(
        manifest_found=True,
        manifest_path=str(manifest),
        sha256_status="pending",
        message="manifest present; SHA256 verification not yet implemented",
    )


def verify_sha256_stub(file_path: Path) -> IntegrityReport:
    """
    SHA256 verification stub.

    Computes hash for logging/diagnostics only; full manifest-driven
    verification is deferred to a later stage.
    """
    if not file_path.is_file():
        return IntegrityReport(
            manifest_found=False,
            manifest_path=None,
            sha256_status="stub",
            message=f"file not found: {file_path}",
        )

    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = digest.hexdigest()
    logger.info("SHA256 stub %s -> %s", file_path, checksum)
    return IntegrityReport(
        manifest_found=True,
        manifest_path=str(file_path),
        sha256_status="stub",
        message=f"SHA256 stub computed: {checksum}",
    )


def inspect_volume_root(root: Path) -> IntegrityReport:
    """Check manifest presence and run SHA256 stub when available."""
    report = check_manifest(root)
    if report.manifest_found and report.manifest_path:
        return verify_sha256_stub(Path(report.manifest_path))
    return report

"""Post-step artifact verification for backup apply."""

from __future__ import annotations

from pathlib import Path

from backup_engine.backup_runtime_log import runtime_log


class ArtifactVerificationError(RuntimeError):
    """Backup step completed but expected artifact is missing or invalid."""


def verify_artifact_file(
    path: Path,
    *,
    label: str,
    min_size: int = 1,
) -> None:
    if not path.is_file():
        raise ArtifactVerificationError(f"{label} artifact missing: {path}")
    size = path.stat().st_size
    if size < min_size:
        raise ArtifactVerificationError(
            f"{label} artifact too small ({size} bytes): {path}"
        )
    runtime_log(f"{label} artifact verified: {path.name} size={size}")

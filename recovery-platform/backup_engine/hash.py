"""SHA256 helpers for backup artifacts and manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Iterable


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_files(
    recovery_root: Path,
    relative_paths: Iterable[str],
) -> Dict[str, str]:
    """Compute SHA256 for multiple files relative to recovery_root."""
    hashes: Dict[str, str] = {}
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        file_path = recovery_root / normalized
        if not file_path.is_file():
            raise FileNotFoundError(f"artifact missing for hashing: {file_path}")
        hashes[normalized] = sha256_file(file_path)
    return hashes

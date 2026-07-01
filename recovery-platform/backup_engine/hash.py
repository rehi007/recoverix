"""SHA256 helpers for backup artifacts and manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

HashProgressCallback = Callable[[Dict[str, Any]], None]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def sha256_file(
    path: Path,
    *,
    chunk_size: int = 1024 * 1024,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Compute SHA256 hex digest of a file."""
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
            done += len(chunk)
            if progress_callback is not None:
                progress_callback(done, total)
    return digest.hexdigest()


def sha256_files(
    recovery_root: Path,
    relative_paths: Iterable[str],
    *,
    progress_callback: Optional[HashProgressCallback] = None,
) -> Dict[str, str]:
    """Compute SHA256 for multiple files relative to recovery_root."""
    hashes: Dict[str, str] = {}
    normalized_paths = [relative.replace("\\", "/") for relative in relative_paths]
    sizes: Dict[str, int] = {}
    total_bytes = 0
    for normalized in normalized_paths:
        file_path = recovery_root / normalized
        if not file_path.is_file():
            raise FileNotFoundError(f"artifact missing for hashing: {file_path}")
        size = file_path.stat().st_size
        sizes[normalized] = size
        total_bytes += size

    completed_bytes = 0
    for index, normalized in enumerate(normalized_paths, start=1):
        file_path = recovery_root / normalized
        file_size = sizes[normalized]

        def _file_progress(done: int, _total: int) -> None:
            if progress_callback is None:
                return
            progress_callback(
                {
                    "relative_path": normalized,
                    "file_index": index,
                    "file_total": len(normalized_paths),
                    "file_bytes_done": done,
                    "file_bytes_total": file_size,
                    "bytes_done": completed_bytes + done,
                    "bytes_total": total_bytes,
                }
            )

        hashes[normalized] = sha256_file(
            file_path,
            progress_callback=_file_progress if progress_callback is not None else None,
        )
        completed_bytes += file_size
    return hashes

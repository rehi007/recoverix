"""Canonical backup finalize transaction (hash, manifest, validation, marker clear)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backup_engine.backup_state import (
    DEFAULT_IMAGE_FILES,
    WINDOWS_USED_DOMAIN_RELATIVE,
    clear_incomplete_backup,
    has_incomplete_backup,
    incomplete_marker_path,
    list_missing_artifacts,
    mark_incomplete_backup,
)
from backup_engine.hash import sha256_file, sha256_files, sha256_text
from backup_engine.manifest import (
    DiskMetadata,
    HashGenerationError,
    ManifestContext,
    ManifestCreationError,
    build_manifest,
    canonical_manifest_json,
    compute_manifest_hash,
    development_mode_enabled,
)
from backup_engine.backup_runtime_log import runtime_log
from common.logger import get_logger

logger = get_logger(__name__)

FINALIZE_LOG_PATH = Path("/var/log/recoverix-backup-finalize.log")
CANONICAL_MANIFEST_REL = Path("manifests/recovery-manifest.json")
LEGACY_MANIFEST_REL = Path("recovery-manifest.json")
LEGACY_METADATA_MANIFEST_REL = Path("metadata/recovery-manifest.json")

CANONICAL_HASH_SIDECARS = {
    "windows": Path("hashes/windows_backup.sha256"),
    "efi": Path("hashes/efi_backup.sha256"),
    "gpt": Path("hashes/gpt_backup.sha256"),
    "manifest": Path("hashes/manifest.sha256"),
}


@dataclass
class FinalizeCheckResult:
    """Result of finalize validation (JSON-serializable)."""

    finalize_ok: bool
    valid_backup_exists: bool
    manifest_exists: bool
    hashes_valid: bool
    incomplete_backup_present: bool
    windows_image_exists: bool = False
    efi_image_exists: bool = False
    gpt_backup_exists: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_json_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json_dict(), indent=indent, sort_keys=True)


def finalize_log(message: str, *, log_path: Path | None = None) -> None:
    """Append one line to recoverix-backup-finalize.log (best-effort)."""
    path = log_path or FINALIZE_LOG_PATH
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} {message}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
    logger.info("finalize: %s", message)


def manifest_path_candidates(recovery_root: Path) -> List[Path]:
    return [
        recovery_root / CANONICAL_MANIFEST_REL,
        recovery_root / LEGACY_METADATA_MANIFEST_REL,
        recovery_root / LEGACY_MANIFEST_REL,
    ]


def find_manifest_path(recovery_root: Path) -> Optional[Path]:
    for candidate in manifest_path_candidates(recovery_root):
        if candidate.is_file():
            return candidate
    return None


def build_canonical_manifest_v1(
    ctx: ManifestContext,
    *,
    sha256_hashes: Dict[str, str],
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build manifests/recovery-manifest.json content (canonical v1 schema)."""
    windows_rel = DEFAULT_IMAGE_FILES["windows"]
    efi_rel = DEFAULT_IMAGE_FILES["efi"]
    gpt_rel = DEFAULT_IMAGE_FILES["gpt"]

    manifest: Dict[str, Any] = {
        "version": 1,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "backup_type": ctx.backup_type,
        "windows_image": windows_rel,
        "efi_image": efi_rel,
        "gpt_backup": gpt_rel,
        "hashes": {
            "windows": str(CANONICAL_HASH_SIDECARS["windows"]),
            "efi": str(CANONICAL_HASH_SIDECARS["efi"]),
            "gpt": str(CANONICAL_HASH_SIDECARS["gpt"]),
            "manifest": str(CANONICAL_HASH_SIDECARS["manifest"]),
        },
        "backup_complete": True,
        "development_mode": development_mode_enabled(),
        # Backward-compatible fields for restore_engine / validation.
        "image_version": "1",
        "backup_version": "1",
        "disk_guid": ctx.disk.disk_guid,
        "disk_model": ctx.disk.disk_model,
        "disk_serial": ctx.disk.disk_serial,
        "disk_size": ctx.disk.disk_size,
        "windows_partition_uuid": ctx.disk.windows_partition_uuid,
        "efi_partition_uuid": ctx.disk.efi_partition_uuid,
        "source_partitions": {
            "windows_partition_uuid": ctx.disk.windows_partition_uuid,
            "efi_partition_uuid": ctx.disk.efi_partition_uuid,
        },
        "image_files": dict(ctx.image_files),
        "image_filenames": dict(ctx.image_files),
        "sha256_hashes": dict(sha256_hashes),
        "tool_versions": dict(ctx.tool_versions),
        "device_id": ctx.device_id(),
    }
    if ctx.restore_baseline_bytes is not None:
        manifest["restore_baseline_bytes"] = int(ctx.restore_baseline_bytes)
    if ctx.source_windows_partition_size_bytes is not None:
        manifest["source_windows_partition_size_bytes"] = int(
            ctx.source_windows_partition_size_bytes
        )
    if ctx.admin_backup:
        manifest["admin_backup"] = dict(ctx.admin_backup)
    if (ctx.recovery_root / WINDOWS_USED_DOMAIN_RELATIVE).is_file():
        manifest["compatibility"] = {
            "windows_used_domain": WINDOWS_USED_DOMAIN_RELATIVE,
        }
    return manifest


def _artifact_non_empty(recovery_root: Path, relative: str) -> bool:
    path = recovery_root / relative.replace("\\", "/")
    return path.is_file() and path.stat().st_size > 0


def _detect_legacy_layout_issues(recovery_root: Path) -> List[str]:
    """Report common partial-backup layout mistakes (fail-closed hints)."""
    issues: List[str] = []
    efi_dir = recovery_root / "efi_backup"
    if efi_dir.is_dir() and not (recovery_root / DEFAULT_IMAGE_FILES["efi"]).is_file():
        issues.append(
            "images/efi_backup.pcl missing (found efi_backup/ directory; EFI backup incomplete)"
        )
    manifests_old = recovery_root / "manifests"
    if manifests_old.is_dir() and not find_manifest_path(recovery_root):
        issues.append("manifests/recovery-manifest.json missing")
    return issues


def validate_finalize_state(
    recovery_root: Path,
    *,
    manifest: Optional[Dict[str, Any]] = None,
    allow_incomplete_marker: bool = False,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> FinalizeCheckResult:
    """
    Validate canonical backup finalize requirements (read-only).

    Success requires all artifacts, manifest, hash sidecars, and hash verification.
    When allow_incomplete_marker is False, incomplete_backup must be absent
    (used by CLI/UI). During finalize transaction step 8, pass allow_incomplete_marker=True.
    """
    errors: List[str] = []
    warnings: List[str] = []

    incomplete = has_incomplete_backup(recovery_root)
    if incomplete and not allow_incomplete_marker:
        errors.append("state/incomplete_backup present")

    windows_ok = _artifact_non_empty(recovery_root, DEFAULT_IMAGE_FILES["windows"])
    efi_ok = _artifact_non_empty(recovery_root, DEFAULT_IMAGE_FILES["efi"])
    gpt_ok = _artifact_non_empty(recovery_root, DEFAULT_IMAGE_FILES["gpt"])

    if not windows_ok:
        errors.append(f"missing or empty: {DEFAULT_IMAGE_FILES['windows']}")
    if not efi_ok:
        errors.append(f"missing or empty: {DEFAULT_IMAGE_FILES['efi']}")
    if not gpt_ok:
        errors.append(f"missing or empty: {DEFAULT_IMAGE_FILES['gpt']}")

    errors.extend(_detect_legacy_layout_issues(recovery_root))

    manifest_path = find_manifest_path(recovery_root)
    manifest_exists = manifest_path is not None
    if not manifest_exists:
        errors.append(f"missing: {CANONICAL_MANIFEST_REL}")

    hashes_valid = True
    for name, rel in CANONICAL_HASH_SIDECARS.items():
        sidecar = recovery_root / rel
        if not sidecar.is_file():
            hashes_valid = False
            errors.append(f"missing hash sidecar: {rel}")
            continue
        digest = sidecar.read_text(encoding="utf-8").strip()
        if not digest:
            hashes_valid = False
            errors.append(f"empty hash sidecar: {rel}")

    if manifest_exists and hashes_valid:
        try:
            loaded = manifest if manifest is not None else json.loads(
                manifest_path.read_text(encoding="utf-8")  # type: ignore[union-attr]
            )
            if not loaded.get("backup_complete"):
                errors.append("manifest backup_complete is not true")
                hashes_valid = False
            declared = loaded.get("sha256_hashes") or {}
            if isinstance(declared, dict):
                normalized_items = [
                    (rel.replace("\\", "/"), expected)
                    for rel, expected in declared.items()
                ]
                sizes: Dict[str, int] = {}
                total_bytes = 0
                for normalized, _expected in normalized_items:
                    artifact = recovery_root / normalized
                    if artifact.is_file():
                        size = artifact.stat().st_size
                        sizes[normalized] = size
                        total_bytes += size

                completed_bytes = 0
                for rel, expected in normalized_items:
                    normalized = rel.replace("\\", "/")
                    artifact = recovery_root / normalized
                    if not artifact.is_file():
                        hashes_valid = False
                        errors.append(f"manifest artifact missing: {rel}")
                        continue

                    file_size = sizes.get(normalized, 0)

                    def _validation_progress(done: int, _total: int) -> None:
                        if progress_callback is None:
                            return
                        progress_callback(
                            {
                                "phase": "hash_validation",
                                "relative_path": normalized,
                                "bytes_done": completed_bytes + done,
                                "bytes_total": total_bytes,
                                "file_bytes_done": done,
                                "file_bytes_total": file_size,
                            }
                        )

                    actual = sha256_file(
                        artifact,
                        progress_callback=_validation_progress
                        if progress_callback is not None
                        else None,
                    )
                    completed_bytes += file_size
                    if actual != expected:
                        hashes_valid = False
                        errors.append(f"hash mismatch for {rel}")
            manifest_digest_path = recovery_root / CANONICAL_HASH_SIDECARS["manifest"]
            if manifest_digest_path.is_file():
                expected_manifest_hash = manifest_digest_path.read_text(encoding="utf-8").strip()
                computed = compute_manifest_hash(loaded)
                if expected_manifest_hash != computed:
                    hashes_valid = False
                    errors.append("manifest.sha256 does not match recovery-manifest.json")
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            hashes_valid = False
            errors.append(f"manifest validation failed: {exc}")

    artifacts_ok = (
        windows_ok
        and efi_ok
        and gpt_ok
        and manifest_exists
        and hashes_valid
        and len(errors) == 0
    )
    valid_backup = artifacts_ok and not incomplete
    finalize_ok = artifacts_ok if allow_incomplete_marker else valid_backup

    return FinalizeCheckResult(
        finalize_ok=finalize_ok,
        valid_backup_exists=valid_backup,
        manifest_exists=manifest_exists,
        hashes_valid=hashes_valid,
        incomplete_backup_present=incomplete,
        windows_image_exists=windows_ok,
        efi_image_exists=efi_ok,
        gpt_backup_exists=gpt_ok,
        errors=errors,
        warnings=warnings,
    )


def write_canonical_manifest_files(
    recovery_root: Path,
    manifest: Dict[str, Any],
) -> Path:
    """Write manifests/recovery-manifest.json and hash sidecars."""
    recovery_root.mkdir(parents=True, exist_ok=True)

    manifests_dir = recovery_root / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    canonical_path = recovery_root / CANONICAL_MANIFEST_REL
    canonical_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    finalize_log(f"manifest generation: wrote {CANONICAL_MANIFEST_REL}")
    runtime_log(f"manifest generation: wrote {CANONICAL_MANIFEST_REL}")

    # Legacy compatibility copies (read-only consumers).
    legacy_root = recovery_root / LEGACY_MANIFEST_REL
    legacy_root.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    metadata_dir = recovery_root / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "recovery-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    hashes_dir = recovery_root / "hashes"
    hashes_dir.mkdir(parents=True, exist_ok=True)

    manifest_hash = compute_manifest_hash(manifest)
    manifest_hash_path = recovery_root / CANONICAL_HASH_SIDECARS["manifest"]
    manifest_hash_path.write_text(manifest_hash + "\n", encoding="utf-8")
    finalize_log("manifest hash: wrote hashes/manifest.sha256")

    (recovery_root / "recovery-manifest.sha256").write_text(manifest_hash + "\n", encoding="utf-8")

    sha256_hashes: Dict[str, str] = manifest.get("sha256_hashes") or {}
    for rel, digest in sha256_hashes.items():
        sidecar = hashes_dir / f"{Path(rel).stem}.sha256"
        sidecar.write_text(str(digest) + "\n", encoding="utf-8")

    return canonical_path


def normalize_runtime_backup_permissions(recovery_root: Path) -> None:
    """
    Make backup outputs readable to the unprivileged runtime TUI user.

    Backup apply runs as root, but runtime validation/menu rendering runs as the
    `recoverix` user. Artifacts must therefore remain world-readable.
    """
    candidates = [recovery_root, *recovery_root.rglob("*")]
    for path in candidates:
        try:
            if path.name == "lost+found":
                continue
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o644)
        except OSError:
            continue


def _invalidate_on_finalize_failure(
    recovery_root: Path,
    reason: str,
    *,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    mark_incomplete_backup(recovery_root, reason, details=details)
    for path in manifest_path_candidates(recovery_root):
        if path.is_file():
            path.unlink()
            finalize_log(f"finalize failure: removed {path.relative_to(recovery_root)}")
    for rel in CANONICAL_HASH_SIDECARS.values():
        sidecar = recovery_root / rel
        if sidecar.is_file():
            sidecar.unlink()
    legacy_hash = recovery_root / "recovery-manifest.sha256"
    if legacy_hash.is_file():
        legacy_hash.unlink()


def run_backup_finalize_transaction(
    ctx: ManifestContext,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> tuple[Dict[str, Any], str, Path]:
    """
    Canonical finalize transaction (steps 5-9 after image backups).

    Order:
      5. sha256 generation
      6. manifests/recovery-manifest.json
      7. hashes/manifest.sha256 (+ per-artifact sidecars)
      8. finalize validation
      9. clear incomplete_backup only on success
    """
    recovery_root = ctx.recovery_root
    finalize_log("finalize start")

    missing = list_missing_artifacts(recovery_root, ctx.artifact_paths())
    if missing:
        msg = f"artifacts incomplete before finalize: {missing}"
        finalize_log(f"finalize failure: {msg}")
        _invalidate_on_finalize_failure(
            recovery_root,
            "artifacts incomplete",
            details={"missing": missing},
        )
        raise ManifestCreationError(msg)

    finalize_log("windows backup complete (artifact present)")
    finalize_log("efi backup complete (artifact present)")
    finalize_log("gpt backup complete (artifact present)")

    try:
        finalize_log("hash generation: start")
        runtime_log("hash generation: start")

        def _hash_generation_progress(event: Dict[str, Any]) -> None:
            if progress_callback is None:
                return
            payload = dict(event)
            payload["phase"] = "hash_generation"
            progress_callback(payload)

        hashes = sha256_files(
            recovery_root,
            ctx.artifact_paths(),
            progress_callback=_hash_generation_progress
            if progress_callback is not None
            else None,
        )
        finalize_log(f"hash generation: complete ({len(hashes)} artifacts)")
        runtime_log(f"hash generation: complete ({len(hashes)} artifacts)")
    except (OSError, FileNotFoundError) as exc:
        finalize_log(f"hash generation: failed ({exc})")
        _invalidate_on_finalize_failure(
            recovery_root,
            "hash generation failed",
            details={"error": str(exc)},
        )
        raise HashGenerationError(f"hash generation failed: {exc}") from exc

    try:
        manifest = build_canonical_manifest_v1(ctx, sha256_hashes=hashes)
        # Keep extended manifest fields for restore validation.
        manifest.update(
            {
                k: v
                for k, v in build_manifest(ctx, sha256_hashes=hashes).items()
                if k not in manifest
            }
        )
        manifest_hash = compute_manifest_hash(manifest)
        path = write_canonical_manifest_files(recovery_root, manifest)
    except (OSError, TypeError, ValueError, ManifestCreationError) as exc:
        finalize_log(f"manifest generation: failed ({exc})")
        _invalidate_on_finalize_failure(
            recovery_root,
            "manifest creation failed",
            details={"error": str(exc)},
        )
        raise ManifestCreationError(f"manifest creation failed: {exc}") from exc

    finalize_log("finalize validation: start")
    check = validate_finalize_state(
        recovery_root,
        manifest=manifest,
        allow_incomplete_marker=True,
        progress_callback=progress_callback,
    )
    if not check.finalize_ok:
        finalize_log(f"finalize validation: failed ({check.errors})")
        _invalidate_on_finalize_failure(
            recovery_root,
            "finalize validation failed",
            details={"errors": check.errors},
        )
        raise ManifestCreationError(
            f"finalize validation failed: {', '.join(check.errors)}"
        )

    finalize_log("finalize validation: passed")
    runtime_log("finalize validation: passed")
    normalize_runtime_backup_permissions(recovery_root)
    finalize_log("permissions normalized for runtime access")
    runtime_log("permissions normalized for runtime access")
    clear_incomplete_backup(recovery_root)
    finalize_log("incomplete_backup cleared")
    runtime_log("incomplete_backup cleared")
    finalize_log("finalize success")
    runtime_log("finalize success")
    return manifest, manifest_hash, path


def run_backup_finalize_check(
    recovery_root: Path,
    *,
    log_path: Path | None = None,
) -> FinalizeCheckResult:
    """Read-only finalize check for CLI (recoverix-backup-finalize-check)."""
    _ = log_path
    return validate_finalize_state(recovery_root)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint: recoverix-backup-finalize-check."""
    _ = argv
    root_env = os.environ.get("RECOVERIX_IMAGE_ROOT")
    if root_env:
        root = Path(root_env)
    else:
        from recovery_runtime.gtk_ui.image_status import probe_recovery_image

        status = probe_recovery_image()
        if not status.mounted or status.mount_point is None:
            result = FinalizeCheckResult(
                finalize_ok=False,
                valid_backup_exists=False,
                manifest_exists=False,
                hashes_valid=False,
                incomplete_backup_present=False,
                errors=["RECOVERY_IMAGE not mounted"],
            )
            print(result.to_json())
            return 1
        root = status.mount_point

    result = run_backup_finalize_check(root)
    print(result.to_json())
    return 0 if result.finalize_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

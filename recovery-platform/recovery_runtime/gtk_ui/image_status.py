"""RECOVERY_IMAGE status probing and UI policy (non-destructive)."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from recovery_runtime.gtk_ui.backup_state import (
    DEFAULT_VALID_BACKUP_MARKER,
    valid_backup_exists,
)
from recovery_runtime.gtk_ui.logging_util import image_status_log

RECOVERY_LABEL = "RECOVERY_IMAGE"
DEFAULT_MOUNT_POINT = Path("/mnt/recoverix-image")


@dataclass
class ImageStatus:
    """Snapshot of RECOVERY_IMAGE partition and backup tree."""

    recovery_image_partition_found: bool
    device: Optional[str] = None
    filesystem: Optional[str] = None
    mount_point: Optional[Path] = None
    mounted: bool = False
    manifest_exists: bool = False
    system_image_exists: bool = False
    esp_image_exists: bool = False
    hashes_exist: bool = False
    valid_backup_exists: bool = False
    errors: List[str] = None

    def to_json_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        if self.mount_point is not None:
            data["mount_point"] = str(self.mount_point)
        return data


@dataclass
class UIButtonState:
    """GTK button state derived from ImageStatus and admin mode."""

    backup_sensitive: bool
    restore_sensitive: bool
    delete_visible: bool
    delete_sensitive: bool
    warning: Optional[str] = None


def _read_proc_mounts() -> List[Tuple[str, str]]:
    mounts: List[Tuple[str, str]] = []
    try:
        with Path("/proc/self/mounts").open("r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2:
                    mounts.append((parts[0], parts[1]))
    except OSError as exc:
        image_status_log(f"error reading /proc/self/mounts: {exc}")
    return mounts


def _blkid_fstype(dev: str) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["blkid", "-o", "value", "-s", "TYPE", dev],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _find_device_by_label(label: str = RECOVERY_LABEL) -> Tuple[Optional[str], Optional[str]]:
    """Locate RECOVERY_IMAGE device using /dev/disk/by-label and blkid."""
    by_label = Path("/dev/disk/by-label") / label
    if by_label.is_symlink():
        try:
            dev = os.path.realpath(str(by_label))
        except OSError:
            dev = str(by_label)
        fstype = _blkid_fstype(dev)
        image_status_log(f"found RECOVERY_IMAGE via by-label: dev={dev}, fstype={fstype or 'unknown'}")
        return dev, fstype

    try:
        out = subprocess.check_output(
            ["blkid", "-o", "export"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        image_status_log(f"blkid export failed: {exc}")
        return None, None

    current_dev: Optional[str] = None
    fstype: Optional[str] = None
    found: Optional[Tuple[str, str]] = None

    for line in out.splitlines():
        if line.startswith("DEVNAME="):
            current_dev = line.split("=", 1)[1].strip()
            fstype = None
        elif line.startswith("TYPE="):
            fstype = line.split("=", 1)[1].strip()
        elif line.startswith("LABEL=") and current_dev:
            val = line.split("=", 1)[1].strip()
            if val == label:
                found = (current_dev, fstype or None)
                break

    if found:
        image_status_log(f"found RECOVERY_IMAGE via blkid: dev={found[0]}, fstype={found[1] or 'unknown'}")
        return found

    return None, None


def _ensure_mounted(dev: str, mount_point: Path = DEFAULT_MOUNT_POINT) -> Tuple[bool, Optional[Path], List[str]]:
    """Ensure RECOVERY_IMAGE is mounted read-only at mount_point."""
    errors: List[str] = []
    for m_dev, m_point in _read_proc_mounts():
        if m_dev == dev:
            mp = Path(m_point)
            image_status_log(f"device {dev} already mounted at {mp}")
            return True, mp, errors

    try:
        mount_point.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"failed to create mount point {mount_point}: {exc}"
        image_status_log(msg)
        errors.append(msg)
        return False, None, errors

    try:
        subprocess.check_call(
            ["mount", "-o", "ro", dev, str(mount_point)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        image_status_log(f"mounted {dev} at {mount_point} (ro)")
        return True, mount_point, errors
    except (OSError, subprocess.SubprocessError) as exc:
        msg = f"mount failed for {dev} at {mount_point}: {exc}"
        image_status_log(msg)
        errors.append(msg)
        return False, None, errors


def _evaluate_backup_tree(root: Path) -> Tuple[bool, bool, bool, bool, bool]:
    """Check for canonical RECOVERY_IMAGE backup tree under root."""
    images_dir = root / "images"
    hashes_dir = root / "hashes"
    metadata_dir = root / "metadata"
    manifests_dir = root / "manifests"

    # `incomplete_backup` is fail-closed: restore must be blocked while it exists.
    incomplete_marker_exists = (root / "state" / "incomplete_backup").is_file()

    # Canonical image filenames.
    windows_image_rel = Path("images/windows_backup.pcl")
    efi_image_rel = Path("images/efi_backup.pcl")

    system_image_exists = (root / windows_image_rel).is_file()  # keep legacy field name
    esp_image_exists = (root / efi_image_rel).is_file()  # keep legacy field name

    gpt_backup_exists = (metadata_dir / "gpt_backup.bin").is_file()

    # Canonical manifest location (manifests/ primary; metadata/ legacy).
    manifest_exists = (manifests_dir / "recovery-manifest.json").is_file() or (
        metadata_dir / "recovery-manifest.json"
    ).is_file()

    # UI expects sidecar SHA256 files.
    hash_files = [
        hashes_dir / "windows_backup.sha256",
        hashes_dir / "efi_backup.sha256",
        hashes_dir / "gpt_backup.sha256",
        hashes_dir / "manifest.sha256",
    ]
    hashes_exist = all(p.is_file() for p in hash_files)

    # Valid when canonical artifacts exist and incomplete marker is absent.
    valid = (
        not incomplete_marker_exists
        and system_image_exists
        and esp_image_exists
        and gpt_backup_exists
        and manifest_exists
        and hashes_exist
        and manifests_dir.is_dir()
        and metadata_dir.is_dir()
    )
    return manifest_exists, system_image_exists, esp_image_exists, hashes_exist, valid


def probe_recovery_image() -> ImageStatus:
    """Probe RECOVERY_IMAGE using device discovery and a read-only mount."""
    override_root = os.environ.get("RECOVERIX_IMAGE_ROOT")
    if override_root:
        root = Path(override_root)
        status = ImageStatus(
            recovery_image_partition_found=root.is_dir(),
            device="mock",
            filesystem=None,
            mount_point=root if root.is_dir() else None,
            mounted=root.is_dir(),
            errors=[],
        )
        if root.is_dir():
            (
                status.manifest_exists,
                status.system_image_exists,
                status.esp_image_exists,
                status.hashes_exist,
                status.valid_backup_exists,
            ) = _evaluate_backup_tree(root)
            image_status_log(
                f"override root probe: root={root}, valid={status.valid_backup_exists}",
            )
        else:
            err = f"RECOVERIX_IMAGE_ROOT={override_root} is not a directory"
            status.errors.append(err)
            image_status_log(err)
        return status

    dev, fstype = _find_device_by_label()
    if not dev:
        msg = "RECOVERY_IMAGE partition not found"
        image_status_log(msg)
        return ImageStatus(
            recovery_image_partition_found=False,
            device=None,
            filesystem=None,
            mount_point=None,
            mounted=False,
            manifest_exists=False,
            system_image_exists=False,
            esp_image_exists=False,
            hashes_exist=False,
            valid_backup_exists=False,
            errors=[msg],
        )

    mounted, mount_point, errors = _ensure_mounted(dev, DEFAULT_MOUNT_POINT)
    status = ImageStatus(
        recovery_image_partition_found=True,
        device=dev,
        filesystem=fstype,
        mount_point=mount_point,
        mounted=mounted,
        manifest_exists=False,
        system_image_exists=False,
        esp_image_exists=False,
        hashes_exist=False,
        valid_backup_exists=False,
        errors=errors,
    )

    if not mounted or mount_point is None:
        return status

    (
        status.manifest_exists,
        status.system_image_exists,
        status.esp_image_exists,
        status.hashes_exist,
        status.valid_backup_exists,
    ) = _evaluate_backup_tree(mount_point)

    image_status_log(
        "probe complete: "
        f"dev={status.device}, mounted={status.mounted}, "
        f"valid_backup={status.valid_backup_exists}",
    )
    return status


def derive_ui_button_state(status: ImageStatus, *, admin_mode: bool) -> UIButtonState:
    """Map ImageStatus + admin flag to GTK button state."""
    if not status.recovery_image_partition_found:
        warning = "RECOVERY_IMAGE partition not found"
        backup_sensitive = False
        restore_sensitive = False
        delete_visible = admin_mode
        delete_sensitive = False
    elif not status.valid_backup_exists:
        warning = None
        backup_sensitive = True
        restore_sensitive = False
        delete_visible = admin_mode
        delete_sensitive = False
    else:
        warning = None
        backup_sensitive = False
        restore_sensitive = True
        delete_visible = admin_mode
        delete_sensitive = admin_mode

    return UIButtonState(
        backup_sensitive=backup_sensitive,
        restore_sensitive=restore_sensitive,
        delete_visible=delete_visible,
        delete_sensitive=delete_sensitive,
        warning=warning,
    )


def effective_status_for_ui(*, admin_mode: bool) -> Tuple[ImageStatus, UIButtonState]:
    """Return (ImageStatus, UIButtonState) with optional mock fallback.

    If RECOVERIX_UI_MOCK=1 is set, bypass RECOVERY_IMAGE probing and use the
    legacy /tmp marker policy instead (development-only fallback).
    """
    if os.environ.get("RECOVERIX_UI_MOCK") == "1":
        marker_valid = valid_backup_exists(DEFAULT_VALID_BACKUP_MARKER)
        image_status_log(
            f"mock fallback enabled (RECOVERIX_UI_MOCK=1), marker_valid={marker_valid}",
        )
        status = ImageStatus(
            recovery_image_partition_found=True,
            device="mock",
            filesystem=None,
            mount_point=Path("/tmp/recoverix-mock"),
            mounted=True,
            manifest_exists=False,
            system_image_exists=False,
            esp_image_exists=False,
            hashes_exist=False,
            valid_backup_exists=marker_valid,
            errors=[],
        )
        buttons = derive_ui_button_state(status, admin_mode=admin_mode)
        return status, buttons

    status = probe_recovery_image()
    buttons = derive_ui_button_state(status, admin_mode=admin_mode)
    return status, buttons


def status_to_cli_json(status: ImageStatus) -> str:
    """Render ImageStatus and derived availability flags as JSON."""
    base = status.to_json_dict()

    if not status.recovery_image_partition_found:
        backup_available = False
        restore_available = False
        delete_available = False
    elif not status.valid_backup_exists:
        backup_available = True
        restore_available = False
        delete_available = False
    else:
        backup_available = False
        restore_available = True
        delete_available = True

    base.update(
        {
            "backup_available": backup_available,
            "restore_available": restore_available,
            "delete_available": delete_available,
        }
    )
    return json.dumps(base, indent=2, sort_keys=True)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for `recoverix-image-status`."""
    _ = argv  # unused for now (reserved for future filters)
    status = probe_recovery_image()
    print(status_to_cli_json(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


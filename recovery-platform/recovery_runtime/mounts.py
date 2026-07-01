"""Read-only mount helpers (no destructive operations in this stage)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from common.command import run_command
from common.logger import get_logger

logger = get_logger(__name__)

_RUNTIME_MOUNT_ROOT = Path("/run/recovery-runtime/mnt")


def mount_point_for_label(label: str) -> Path:
    """Return a stable mount path under /run for a volume label."""
    safe = label.lower().replace(" ", "_")
    return _RUNTIME_MOUNT_ROOT / safe


def plan_mount(device_path: str, mount_point: Path, *, read_only: bool = True) -> str:
    """Return a mount command string for logging/planning only."""
    options = "ro" if read_only else "rw"
    return f"mount -o {options} {device_path} {mount_point}"


def mount_readonly(
    device_path: str,
    mount_point: Path,
    *,
    dry_run: bool = True,
) -> bool:
    """
    Mount a device read-only.

    Default dry_run=True: logs the command without executing.
    """
    mount_point.mkdir(parents=True, exist_ok=True)
    command = ["mount", "-o", "ro", device_path, str(mount_point)]
    display = plan_mount(device_path, mount_point, read_only=True)

    if dry_run:
        run_command(command, dry_run=True)
        logger.info("[DRY-RUN] %s", display)
        return False

    result = run_command(command, dry_run=False, confirmed=True)
    success = result.returncode == 0
    if success:
        logger.info("mounted %s -> %s", device_path, mount_point)
    else:
        logger.error("mount failed: %s", result.stderr.strip())
    return success


def _mounted_path_for_device(device_path: str) -> Optional[Path]:
    """Return the active mountpoint for a device if it is already mounted."""
    try:
        with Path("/proc/self/mounts").open("r", encoding="utf-8") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device_path:
                    return Path(parts[1])
    except OSError as exc:
        logger.warning("failed to read /proc/self/mounts: %s", exc)
    return None


def ensure_readonly_mount(
    device_path: str,
    volume_mountpoint: Optional[str],
    label: str,
) -> Optional[Path]:
    """
    Return an accessible mount path for a recovery volume.

    Preference order:
    1. Existing discovered mountpoint from lsblk
    2. Active mountpoint found in /proc/self/mounts
    3. Read-only mount under /run/recovery-runtime/mnt/<label>
    """
    if volume_mountpoint:
        mount = Path(volume_mountpoint)
        if mount.exists():
            return mount

    mounted_path = _mounted_path_for_device(device_path)
    if mounted_path and mounted_path.exists():
        return mounted_path

    if os.geteuid() != 0:
        logger.info(
            "skipping readonly mount for %s as non-root user (euid=%s)",
            device_path,
            os.geteuid(),
        )
        return None

    mount_point = mount_point_for_label(label)
    if mount_readonly(device_path, mount_point, dry_run=False):
        return mount_point
    return None


def resolve_mount_path(volume_mountpoint: Optional[str], label: str) -> Optional[Path]:
    """Use existing mountpoint or planned runtime mount path."""
    if volume_mountpoint:
        return Path(volume_mountpoint)
    return mount_point_for_label(label)

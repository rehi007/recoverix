"""Backup size estimation from NTFS used space (not full partition size)."""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.command import run_readonly
from common.logger import get_logger

logger = get_logger(__name__)

GPT_OVERHEAD_BYTES = 1024 * 1024
WINDOWS_BACKUP_SIZE_RATIO = 0.75
WINDOWS_BACKUP_HEADROOM_BYTES = 2 * 1024**3

_BYTE_RE = re.compile(r"(\d+)\s*(?:\([^)]*\))?")


@dataclass
class BackupSizeEstimate:
    """Dry-run backup space projection."""

    estimated_used_bytes: int
    estimated_required_bytes: int
    estimated_required_gb: float
    estimation_method: str
    estimation_warning: Optional[str] = None
    recovery_image_free_bytes: Optional[int] = None
    can_backup: bool = True
    reason: Optional[str] = None
    estimation_details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_used_bytes": self.estimated_used_bytes,
            "estimated_required_bytes": self.estimated_required_bytes,
            "estimated_required_gb": self.estimated_required_gb,
            "estimation_method": self.estimation_method,
            "estimation_warning": self.estimation_warning,
            "recovery_image_free_bytes": self.recovery_image_free_bytes,
            "can_backup": self.can_backup,
            "reason": self.reason,
            "estimation_details": self.estimation_details,
        }


def _log_probe(probes: List[Dict[str, Any]], entry: Dict[str, Any]) -> None:
    probes.append(entry)
    tool = entry.get("tool", entry.get("method", "?"))
    status = entry.get("status", "?")
    detail = entry.get("detail", "")
    logger.info("backup space probe [%s]: %s %s", tool, status, detail)


def _first_int_after_label(text: str, label: str) -> Optional[int]:
    for line in text.splitlines():
        if label.lower() in line.lower():
            m = _BYTE_RE.search(line.split(":", 1)[-1])
            if m:
                return int(m.group(1))
    return None


def _parse_ntfsinfo(stdout: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (used_bytes, volume_size_bytes) from ntfsinfo volume output."""
    volume_size = _first_int_after_label(stdout, "Volume Size")
    used_direct: Optional[int] = None
    for line in stdout.splitlines():
        low = line.lower().strip()
        if low.startswith("percent") and "used" in low:
            continue
        if re.match(r"^\s*used\s+space\s*:", line, re.I):
            m = _BYTE_RE.search(line.split(":", 1)[-1])
            if m:
                used_direct = int(m.group(1))
                break
    if used_direct is not None:
        return used_direct, volume_size

    percent = None
    for line in stdout.splitlines():
        low = line.lower()
        if "percent" in low and "used" in low:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", line)
            if m:
                percent = float(m.group(1))
                break
    if percent is not None and volume_size is not None and volume_size > 0:
        return int(volume_size * percent / 100.0), volume_size
    return None, volume_size


def _parse_ntfscluster_info(stdout: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse ntfscluster -i (volume info) for used/total bytes."""
    total = None
    used = None
    for line in stdout.splitlines():
        low = line.lower()
        if "bytes in use" in low or "in use" in low and "byte" in low:
            m = _BYTE_RE.search(line)
            if m:
                used = int(m.group(1))
        if "volume size" in low or "total" in low and "byte" in low:
            m = _BYTE_RE.search(line)
            if m and total is None:
                total = int(m.group(1))
        m_pct = re.search(r"(\d+)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)\s*clusters?\s+are\s+in\s+use", line, re.I)
        if m_pct and total is None:
            # cluster-based; need cluster size — handled below via percent line
            pass
        m_cl = re.search(
            r"(\d+)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)\s*clusters?\s+are\s+in\s+use",
            line,
            re.I,
        )
        if m_cl and total:
            pct = float(m_cl.group(2))
            used = int(total * pct / 100.0)
    if used is None:
        m_free = re.search(r"(\d+)\s+bytes.*free\s+space", stdout, re.I)
        volume_size_line = _first_int_after_label(stdout, "Volume Size")
        if m_free and volume_size_line:
            total = volume_size_line
            used = total - int(m_free.group(1))
    return used, total


def _df_bytes(path: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (total, used, avail) via df -B1."""
    result = run_readonly(["df", "-B1", "--output=size,used,avail", path])
    if result.returncode != 0:
        return None, None, None
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None, None, None
    parts = lines[-1].split()
    if len(parts) < 3:
        return None, None, None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None, None, None


def _lsblk_used_bytes(
    device: str,
    partition_size: Optional[int],
    probes: List[Dict[str, Any]],
) -> Optional[int]:
    result = run_readonly(
        ["lsblk", "-b", "-n", "-o", "PATH,SIZE,FSUSE%,FSAVAIL", device],
    )
    _log_probe(
        probes,
        {
            "tool": "lsblk",
            "argv": result.argv,
            "status": "ok" if result.returncode == 0 else "fail",
            "returncode": result.returncode,
            "stdout_head": (result.stdout or "")[:500],
            "stderr_head": (result.stderr or "")[:300],
        },
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            size = int(parts[1])
            fsuse = parts[2].rstrip("%")
            if fsuse in ("-", ""):
                continue
            pct = float(fsuse)
        except ValueError:
            continue
        base = partition_size if partition_size else size
        return int(base * pct / 100.0)
    return None


def _try_ntfsinfo(
    device: str,
    probes: List[Dict[str, Any]],
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    """ntfsinfo volume stats. NOTE: -m is MFT dump only — do not use for used space."""
    if not shutil.which("ntfsinfo"):
        _log_probe(probes, {"tool": "ntfsinfo", "status": "skip", "detail": "binary not found"})
        return None, None, None, None

    for argv in (["ntfsinfo", "-f", device], ["ntfsinfo", device]):
        result = run_readonly(argv)
        used, total = (None, None)
        if result.returncode == 0:
            used, total = _parse_ntfsinfo(result.stdout)
        _log_probe(
            probes,
            {
                "tool": "ntfsinfo",
                "argv": list(argv),
                "status": "ok" if result.returncode == 0 and used else "fail",
                "returncode": result.returncode,
                "parsed_used_bytes": used,
                "parsed_total_bytes": total,
                "stdout_head": (result.stdout or "")[:800],
                "stderr_head": (result.stderr or "")[:300],
            },
        )
        if used is not None and used > 0:
            free = (total - used) if total is not None and total >= used else None
            return used, total, free, "ntfs_used_space"
    return None, None, None, None


def _try_ntfscluster(
    device: str,
    probes: List[Dict[str, Any]],
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    if not shutil.which("ntfscluster"):
        _log_probe(probes, {"tool": "ntfscluster", "status": "skip", "detail": "binary not found"})
        return None, None, None, None
    result = run_readonly(["ntfscluster", "-i", device])
    used, total = (None, None)
    if result.returncode == 0:
        used, total = _parse_ntfscluster_info(result.stdout)
    _log_probe(
        probes,
        {
            "tool": "ntfscluster",
            "argv": ["ntfscluster", "-i", device],
            "status": "ok" if result.returncode == 0 and used else "fail",
            "returncode": result.returncode,
            "parsed_used_bytes": used,
            "parsed_total_bytes": total,
            "stdout_head": (result.stdout or "")[:800],
            "stderr_head": (result.stderr or "")[:300],
        },
    )
    if used is not None and used > 0:
        free = (total - used) if total is not None and total >= used else None
        return used, total, free, "ntfscluster_used_space"
    return None, None, None, None


def _try_ro_mount_df(
    device: str,
    probes: List[Dict[str, Any]],
) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[str]]:
    """Last-resort: read-only mount + df (logged; unmount always attempted)."""
    if not shutil.which("mount"):
        return None, None, None, None
    mount_dir = Path(tempfile.mkdtemp(prefix="recoverix_ntfs_probe_"))
    try:
        mount_result = run_readonly(["mount", "-o", "ro", device, str(mount_dir)])
        _log_probe(
            probes,
            {
                "tool": "mount_ro_df",
                "argv": ["mount", "-o", "ro", device, str(mount_dir)],
                "status": "ok" if mount_result.returncode == 0 else "fail",
                "returncode": mount_result.returncode,
                "stderr_head": (mount_result.stderr or "")[:300],
            },
        )
        if mount_result.returncode != 0:
            return None, None, None, None
        total, used, avail = _df_bytes(str(mount_dir))
        _log_probe(
            probes,
            {
                "tool": "df",
                "path": str(mount_dir),
                "status": "ok" if used else "fail",
                "total_bytes": total,
                "used_bytes": used,
                "avail_bytes": avail,
            },
        )
        if used is not None and used > 0:
            return used, total, avail, "ro_mount_df_used_space"
        return None, None, None, None
    finally:
        run_readonly(["umount", str(mount_dir)])
        try:
            mount_dir.rmdir()
        except OSError:
            pass


def estimate_ntfs_used_bytes(
    device: str,
    *,
    mountpoint: Optional[str],
    partition_size: Optional[int],
) -> Tuple[int, str, Optional[str], Dict[str, Any]]:
    """
    Estimate NTFS used bytes. Never prefers full partition size unless fallback.

    Returns (used_bytes, method, warning, details).
    """
    probes: List[Dict[str, Any]] = []
    details: Dict[str, Any] = {
        "windows_device": device,
        "windows_mountpoint": mountpoint,
        "partition_size_bytes": partition_size,
        "probes": probes,
    }

    if mountpoint:
        total, used, avail = _df_bytes(mountpoint)
        _log_probe(
            probes,
            {
                "tool": "df",
                "path": mountpoint,
                "status": "ok" if used else "fail",
                "total_bytes": total,
                "used_bytes": used,
                "avail_bytes": avail,
            },
        )
        logger.info(
            "NTFS df %s: total=%s used=%s free=%s",
            mountpoint,
            total,
            used,
            avail,
        )
        if used is not None and used > 0:
            details.update(
                {
                    "windows_total_bytes": total,
                    "windows_used_bytes": used,
                    "windows_free_bytes": avail,
                    "selected_method": "df_used_space",
                }
            )
            return used, "df_used_space", None, details

    for probe_fn, method_key in (
        (_try_ntfsinfo, "ntfsinfo"),
        (_try_ntfscluster, "ntfscluster"),
    ):
        used, total, free, method = probe_fn(device, probes)
        if used is not None and used > 0 and method:
            logger.info(
                "NTFS %s %s: total=%s used=%s free=%s",
                method_key,
                device,
                total,
                used,
                free,
            )
            details.update(
                {
                    "windows_total_bytes": total,
                    "windows_used_bytes": used,
                    "windows_free_bytes": free,
                    "selected_method": method,
                }
            )
            return used, method, None, details

    used = _lsblk_used_bytes(device, partition_size, probes)
    if used is not None and used > 0:
        details.update(
            {
                "windows_used_bytes": used,
                "windows_total_bytes": partition_size,
                "selected_method": "lsblk_fsuse_percent",
            }
        )
        logger.info("NTFS lsblk %s: used=%s (partition_size=%s)", device, used, partition_size)
        return used, "lsblk_fsuse_percent", None, details

    used, total, free, method = _try_ro_mount_df(device, probes)
    if used is not None and used > 0 and method:
        details.update(
            {
                "windows_total_bytes": total,
                "windows_used_bytes": used,
                "windows_free_bytes": free,
                "selected_method": method,
            }
        )
        return used, method, None, details

    warn = (
        "NTFS used-space probes failed; refusing to estimate from full Windows partition size"
    )
    logger.warning(warn)
    details.update(
        {
            "windows_total_bytes": partition_size,
            "windows_used_bytes": 0,
            "windows_free_bytes": None,
            "selected_method": "ntfs_usage_unavailable",
            "fallback": False,
        }
    )
    return 0, "ntfs_usage_unavailable", warn, details


def estimate_efi_backup_bytes(
    device: str,
    *,
    mountpoint: Optional[str],
    partition_size: Optional[int],
) -> int:
    """EFI images are small; prefer df used, else partition size."""
    if mountpoint:
        _total, used, _avail = _df_bytes(mountpoint)
        if used is not None and used > 0:
            return used
    if partition_size:
        return int(partition_size)
    return 512 * 1024 * 1024


def estimate_recovery_image_free_bytes(
    *,
    device: str,
    mountpoint: Optional[str],
    fstype: Optional[str],
    probes: Optional[List[Dict[str, Any]]] = None,
) -> Optional[int]:
    probe_list = probes if probes is not None else []
    if mountpoint:
        _total, _used, avail = _df_bytes(mountpoint)
        _log_probe(
            probe_list,
            {
                "tool": "df_recovery",
                "path": mountpoint,
                "avail_bytes": avail,
                "status": "ok" if avail is not None else "fail",
            },
        )
        logger.info("RECOVERY_IMAGE df %s: free=%s", mountpoint, avail)
        if avail is not None:
            return avail
    if fstype and "ext" in fstype.lower() and shutil.which("tune2fs"):
        result = run_readonly(["tune2fs", "-l", device])
        block_size = 4096
        free_blocks = None
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("Block size:"):
                    m = re.search(r"(\d+)", line)
                    if m:
                        block_size = int(m.group(1))
                elif line.startswith("Free blocks:"):
                    m = re.search(r"(\d+)", line)
                    if m:
                        free_blocks = int(m.group(1))
        free = free_blocks * block_size if free_blocks is not None else None
        _log_probe(
            probe_list,
            {
                "tool": "tune2fs",
                "device": device,
                "free_bytes": free,
                "status": "ok" if free is not None else "fail",
            },
        )
        return free
    return None


def estimate_backup_space(layout: Any) -> BackupSizeEstimate:
    """Compute backup requirement from used space and RECOVERY_IMAGE free space."""
    windows = layout.windows
    efi = layout.efi
    recovery = layout.recovery_image

    probes: List[Dict[str, Any]] = []
    win_used, method, warn, win_details = estimate_ntfs_used_bytes(
        windows.path,
        mountpoint=windows.mountpoint,
        partition_size=windows.size,
    )
    probes.extend(win_details.get("probes", []))

    efi_bytes = estimate_efi_backup_bytes(
        efi.path,
        mountpoint=efi.mountpoint,
        partition_size=efi.size,
    )
    windows_required = (
        int(win_used * WINDOWS_BACKUP_SIZE_RATIO) + WINDOWS_BACKUP_HEADROOM_BYTES
        if win_used > 0
        else 0
    )
    required = GPT_OVERHEAD_BYTES + efi_bytes + windows_required
    required_gb = round(required / (1024**3), 2)

    free = estimate_recovery_image_free_bytes(
        device=recovery.path,
        mountpoint=recovery.mountpoint,
        fstype=recovery.fstype,
        probes=probes,
    )

    can_backup = True
    reason = None
    if free is not None and required > free:
        can_backup = False
        reason = "insufficient recovery image space"

    if method == "ntfs_usage_unavailable":
        can_backup = False
        reason = warn
    elif warn:
        reason = warn if reason is None else f"{reason}; {warn}"

    details: Dict[str, Any] = {
        "windows": win_details,
        "efi_backup_bytes": efi_bytes,
        "gpt_overhead_bytes": GPT_OVERHEAD_BYTES,
        "windows_backup_size_ratio": WINDOWS_BACKUP_SIZE_RATIO,
        "windows_backup_headroom_bytes": WINDOWS_BACKUP_HEADROOM_BYTES,
        "windows_required_bytes": windows_required,
        "recovery_image_free_bytes": free,
        "probes": probes,
        "selected_windows_method": method,
    }
    logger.info(
        "backup space estimate: method=%s windows_used=%s required_gb=%s free_recovery=%s can_backup=%s",
        method,
        win_used,
        required_gb,
        free,
        can_backup,
    )

    return BackupSizeEstimate(
        estimated_used_bytes=win_used,
        estimated_required_bytes=required,
        estimated_required_gb=required_gb,
        estimation_method=method,
        estimation_warning=warn,
        recovery_image_free_bytes=free,
        can_backup=can_backup,
        reason=reason,
        estimation_details=details,
    )


def estimate_required_bytes(layout: Any) -> int:
    """Backward-compatible total bytes helper (uses used-space policy)."""
    return estimate_backup_space(layout).estimated_required_bytes

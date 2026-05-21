"""Block device discovery via lsblk/blkid (Linux only)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from common.command import run_readonly
from common.logger import get_logger
from partition_manager.models import LABEL_RECOVERY_IMAGE, LABEL_RECOVERY_LINUX

logger = get_logger(__name__)

_LSBLK_CMD = [
    "lsblk",
    "--json",
    "--bytes",
    "-o",
    "NAME,PATH,SIZE,FSTYPE,LABEL,MOUNTPOINT,TYPE",
]


@dataclass(frozen=True)
class DiscoveredVolume:
    """A block device or partition discovered on the system."""

    name: str
    path: str
    label: Optional[str]
    fstype: Optional[str]
    size: Optional[int]
    mountpoint: Optional[str]
    device_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "label": self.label,
            "fstype": self.fstype,
            "size": self.size,
            "mountpoint": self.mountpoint,
            "device_type": self.device_type,
        }


def require_linux() -> None:
    if sys.platform != "linux":
        raise RuntimeError(f"recovery runtime requires Linux (found {sys.platform})")


def _ensure_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _walk_blockdevices(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []
    for node in nodes:
        flat.append(node)
        children = node.get("children")
        if children:
            flat.extend(_walk_blockdevices(_ensure_list(children)))
    return flat


def parse_lsblk_json(payload: str) -> List[DiscoveredVolume]:
    """Parse lsblk --json output into discovered volumes."""
    if not payload.strip():
        return []

    data = json.loads(payload)
    devices = _walk_blockdevices(_ensure_list(data.get("blockdevices")))
    volumes: List[DiscoveredVolume] = []

    for dev in devices:
        path = dev.get("path") or f"/dev/{dev.get('name', '')}"
        label = dev.get("label") or None
        if label == "":
            label = None
        mount = dev.get("mountpoint") or None
        if mount == "":
            mount = None
        size = dev.get("size")
        volumes.append(
            DiscoveredVolume(
                name=str(dev.get("name", "")),
                path=str(path),
                label=str(label).upper() if label else None,
                fstype=dev.get("fstype") or None,
                size=int(size) if size is not None else None,
                mountpoint=mount,
                device_type=dev.get("type"),
            )
        )
    return volumes


def run_lsblk() -> str:
    """Execute lsblk and return JSON stdout."""
    result = run_readonly(_LSBLK_CMD)
    if result.returncode != 0:
        logger.warning("lsblk failed: %s", result.stderr.strip())
        return ""
    return result.stdout


def run_blkid_probe(device_path: str) -> Optional[str]:
    """Probe a single device label with blkid (read-only)."""
    result = run_readonly(["blkid", "-o", "value", "-s", "LABEL", device_path])
    if result.returncode != 0:
        return None
    label = result.stdout.strip()
    return label.upper() if label else None


def enrich_labels(volumes: List[DiscoveredVolume]) -> List[DiscoveredVolume]:
    """Fill missing labels using blkid for partitions without lsblk LABEL."""
    enriched: List[DiscoveredVolume] = []
    for vol in volumes:
        label = vol.label
        if not label and vol.device_type == "part":
            label = run_blkid_probe(vol.path)
        enriched.append(
            DiscoveredVolume(
                name=vol.name,
                path=vol.path,
                label=label,
                fstype=vol.fstype,
                size=vol.size,
                mountpoint=vol.mountpoint,
                device_type=vol.device_type,
            )
        )
    return enriched


def discover_volumes() -> List[DiscoveredVolume]:
    """Discover block devices using lsblk with blkid label fallback."""
    require_linux()
    payload = run_lsblk()
    volumes = parse_lsblk_json(payload)
    return enrich_labels(volumes)


def find_by_label(
    volumes: List[DiscoveredVolume],
    label: str,
) -> Optional[DiscoveredVolume]:
    target = label.upper()
    matches = [vol for vol in volumes if vol.label == target]
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning("multiple volumes with label %s; using first %s", label, matches[0].path)
    return matches[0]


def discover_recovery_volumes() -> tuple[Optional[DiscoveredVolume], Optional[DiscoveredVolume], List[DiscoveredVolume]]:
    """Locate RECOVERY_IMAGE and RECOVERY_LINUX volumes."""
    volumes = discover_volumes()
    image = find_by_label(volumes, LABEL_RECOVERY_IMAGE)
    linux = find_by_label(volumes, LABEL_RECOVERY_LINUX)
    return image, linux, volumes

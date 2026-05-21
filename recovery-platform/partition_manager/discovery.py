"""Read-only partition discovery for Windows (GPT / EFI / OS roles)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from common.command import run_command, run_readonly
from common.logger import get_logger, setup_logging
from partition_manager.models import (
    EFI_BOOT_RELATIVE,
    GPT_EFI_SYSTEM,
    GPT_MICROSOFT_RESERVED,
    LABEL_RECOVERY_IMAGE,
    LABEL_RECOVERY_LINUX,
    WINDOWS_SYSTEM_HIVE,
    DiscoveryResult,
    PartitionRecord,
    PartitionState,
    missing_record,
    unsupported_record,
)
from validation.partition_validation import validate_partition_discovery

logger = get_logger(__name__)

_POWERSHELL = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

_COLLECT_SCRIPT = r"""
$disks = @(Get-Disk | Select-Object Number, FriendlyName, PartitionStyle, Size, SerialNumber)
$partitions = @(Get-Partition | Select-Object DiskNumber, PartitionNumber, DriveLetter, Size, GptType, Type, IsSystem, IsHidden, IsBoot, @{n='AccessPaths';e={@($_.AccessPaths)}})
$volumes = @(Get-Volume | Where-Object { $_.DriveLetter } | Select-Object DriveLetter, FileSystem, FileSystemLabel, Size)
@{ disks = $disks; partitions = $partitions; volumes = $volumes } | ConvertTo-Json -Depth 6 -Compress
"""


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str], *, dry_run: bool) -> Any: ...


def _normalize_guid(value: Optional[str]) -> str:
    if not value:
        return ""
    text = str(value).strip().lower()
    if not text.startswith("{"):
        text = "{" + text
    if not text.endswith("}"):
        text = text + "}"
    return text


def _ensure_list(value: Any) -> List[Dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def parse_storage_layout(payload: str) -> Dict[str, List[Dict[str, Any]]]:
    """Parse combined Get-Disk / Get-Partition / Get-Volume JSON."""
    if not payload.strip():
        return {"disks": [], "partitions": [], "volumes": []}

    data = json.loads(payload)
    return {
        "disks": _ensure_list(data.get("disks")),
        "partitions": _ensure_list(data.get("partitions")),
        "volumes": _ensure_list(data.get("volumes")),
    }


def _volume_by_letter(volumes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for vol in volumes:
        letter = vol.get("DriveLetter")
        if letter:
            mapping[str(letter).rstrip(":").upper()] = vol
    return mapping


def _partition_volume(
    partition: Dict[str, Any],
    volumes_by_letter: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    letter = partition.get("DriveLetter")
    if not letter:
        return None
    return volumes_by_letter.get(str(letter).rstrip(":").upper())


def _access_paths(partition: Dict[str, Any]) -> List[str]:
    paths = partition.get("AccessPaths") or []
    if isinstance(paths, str):
        return [paths]
    return [str(path) for path in paths if path]


def _path_candidates(partition: Dict[str, Any], relative: str) -> List[str]:
    candidates: List[str] = []
    letter = partition.get("DriveLetter")
    if letter:
        root = f"{str(letter).rstrip(':')}:\\"
        candidates.append(root + relative.replace("/", "\\"))

    for path in _access_paths(partition):
        normalized = str(path).rstrip("\\")
        candidates.append(f"{normalized}\\{relative}")

    return candidates


def _base_record_fields(
    part: Dict[str, Any],
    *,
    volume: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    disk_number = int(part.get("DiskNumber", -1))
    partition_number = int(part.get("PartitionNumber", -1))
    gpt_type = _normalize_guid(part.get("GptType")) or None
    file_system = (volume or {}).get("FileSystem")
    label = (volume or {}).get("FileSystemLabel")
    drive_letter = part.get("DriveLetter")
    drive_text = str(drive_letter).rstrip(":") if drive_letter else None
    size = part.get("Size")
    size_bytes = int(size) if size is not None else None
    return {
        "disk_number": disk_number,
        "partition_number": partition_number,
        "gpt_type": gpt_type,
        "file_system": file_system,
        "label": label,
        "drive_letter": drive_text,
        "size_bytes": size_bytes,
        "access_paths": tuple(_access_paths(part)),
    }


def _is_windows_os_partition(
    part: Dict[str, Any],
    volume: Optional[Dict[str, Any]],
    *,
    path_exists: Callable[[str], bool],
) -> bool:
    file_system = (volume or {}).get("FileSystem")
    if not file_system or str(file_system).upper() != "NTFS":
        return False

    for hive_path in _path_candidates(part, WINDOWS_SYSTEM_HIVE):
        lower = hive_path.lower()
        marker = "\\windows\\"
        if marker not in lower:
            continue
        windows_root = hive_path[: lower.index(marker) + len(marker.rstrip("\\"))]
        if path_exists(windows_root) and path_exists(hive_path):
            return True
    return False


def _is_efi_partition(
    part: Dict[str, Any],
    volume: Optional[Dict[str, Any]],
    *,
    path_exists: Callable[[str], bool],
) -> bool:
    gpt_type = _normalize_guid(part.get("GptType"))
    if gpt_type != _normalize_guid(GPT_EFI_SYSTEM):
        return False

    file_system = (volume or {}).get("FileSystem")
    if not file_system or str(file_system).upper() != "FAT32":
        return False

    return any(
        path_exists(candidate)
        for candidate in _path_candidates(part, EFI_BOOT_RELATIVE)
    )


def classify_partitions(
    layout: Dict[str, List[Dict[str, Any]]],
    *,
    path_exists: Callable[[str], bool],
) -> DiscoveryResult:
    """Classify partitions by role using read-only metadata and path probes."""
    partitions = layout.get("partitions", [])
    volumes = layout.get("volumes", [])
    volumes_by_letter = _volume_by_letter(volumes)

    efi_candidates: List[PartitionRecord] = []
    msr_candidates: List[PartitionRecord] = []
    windows_candidates: List[PartitionRecord] = []
    recovery_image: Optional[PartitionRecord] = None
    recovery_linux: Optional[PartitionRecord] = None

    for part in partitions:
        volume = _partition_volume(part, volumes_by_letter)
        fields = _base_record_fields(part, volume=volume)
        gpt_type = fields["gpt_type"] or ""
        label = fields["label"]

        if gpt_type == _normalize_guid(GPT_MICROSOFT_RESERVED):
            msr_candidates.append(
                PartitionRecord(role="msr", state=PartitionState.FOUND.value, **fields)
            )
            continue

        if label and str(label).upper() == LABEL_RECOVERY_IMAGE:
            recovery_image = PartitionRecord(
                role="recovery_image",
                state=PartitionState.FOUND.value,
                **fields,
            )
            continue

        if label and str(label).upper() == LABEL_RECOVERY_LINUX:
            recovery_linux = PartitionRecord(
                role="recovery_linux",
                state=PartitionState.FOUND.value,
                **fields,
            )
            continue

        if _is_efi_partition(part, volume, path_exists=path_exists):
            efi_candidates.append(
                PartitionRecord(role="efi", state=PartitionState.FOUND.value, **fields)
            )
            continue

        if _is_windows_os_partition(part, volume, path_exists=path_exists):
            windows_candidates.append(
                PartitionRecord(role="windows", state=PartitionState.FOUND.value, **fields)
            )

    return DiscoveryResult(
        efi_partition=_select_unique(efi_candidates, role="efi"),
        msr_partition=_select_unique(msr_candidates, role="msr"),
        windows_partition=_select_windows(windows_candidates),
        recovery_image_partition=recovery_image,
        recovery_linux_partition=recovery_linux,
        status="FAIL",
        dry_run=False,
    )


def _select_unique(
    candidates: List[PartitionRecord],
    *,
    role: str,
) -> Optional[PartitionRecord]:
    if not candidates:
        return missing_record(role, reason=f"No {role} partition matched criteria")
    if len(candidates) == 1:
        return candidates[0]
    first = candidates[0]
    return unsupported_record(
        role,
        disk_number=first.disk_number,
        partition_number=first.partition_number,
        reason=f"Multiple {role} candidates detected ({len(candidates)})",
        gpt_type=first.gpt_type,
        file_system=first.file_system,
        label=first.label,
        drive_letter=first.drive_letter,
        size_bytes=first.size_bytes,
        access_paths=first.access_paths,
    )


def _select_windows(
    candidates: List[PartitionRecord],
) -> Optional[PartitionRecord]:
    if not candidates:
        return missing_record("windows", reason="No Windows OS partition matched criteria")
    if len(candidates) == 1:
        return candidates[0]
    first = candidates[0]
    return unsupported_record(
        "windows",
        disk_number=first.disk_number,
        partition_number=first.partition_number,
        reason=f"Multiple Windows OS candidates detected ({len(candidates)})",
        gpt_type=first.gpt_type,
        file_system=first.file_system,
        label=first.label,
        drive_letter=first.drive_letter,
        size_bytes=first.size_bytes,
        access_paths=first.access_paths,
    )


def _run_command(command: Sequence[str], *, dry_run: bool) -> Any:
    if dry_run:
        return run_command(list(command), dry_run=True)
    return run_readonly(list(command))


def _run_storage_query(*, dry_run: bool) -> str:
    result = _run_command(_POWERSHELL + [_COLLECT_SCRIPT], dry_run=dry_run)
    if result.dry_run:
        return ""
    if result.returncode != 0:
        logger.warning("storage query failed: %s", result.stderr.strip())
        return ""
    return result.stdout


def _probe_firmware_boot_entries(*, dry_run: bool) -> None:
    """Read-only firmware boot manager enumeration for diagnostics."""
    result = _run_command(["bcdedit", "/enum", "firmware"], dry_run=dry_run)
    if result.dry_run:
        return
    if result.returncode == 0:
        logger.debug("bcdedit firmware enum captured (%d bytes)", len(result.stdout))
    else:
        logger.warning("bcdedit firmware enum failed: %s", result.stderr.strip())


def _default_path_exists(path: str, *, dry_run: bool) -> bool:
    if dry_run:
        logger.info("[DRY-RUN] would Test-Path: %s", path)
        return False

    escaped = path.replace("'", "''")
    script = f"Test-Path -LiteralPath '{escaped}' -PathType Leaf"
    result = _run_command(_POWERSHELL + [script], dry_run=False)
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def discover_partitions(*, dry_run: bool = True) -> DiscoveryResult:
    """Collect storage layout and classify partition roles (read-only)."""
    if sys.platform != "win32":
        logger.warning("partition discovery requires Windows; found %s", sys.platform)
        return DiscoveryResult(
            efi_partition=missing_record("efi", reason="Not running on Windows"),
            msr_partition=missing_record("msr", reason="Not running on Windows"),
            windows_partition=missing_record("windows", reason="Not running on Windows"),
            recovery_image_partition=None,
            recovery_linux_partition=None,
            status="FAIL",
            dry_run=dry_run,
        )

    _probe_firmware_boot_entries(dry_run=dry_run)

    if dry_run:
        _run_storage_query(dry_run=True)
        logger.info("dry-run mode: skipping live classification")
        return DiscoveryResult(
            efi_partition=missing_record("efi", reason="Dry-run mode"),
            msr_partition=missing_record("msr", reason="Dry-run mode"),
            windows_partition=missing_record("windows", reason="Dry-run mode"),
            recovery_image_partition=None,
            recovery_linux_partition=None,
            status="FAIL",
            dry_run=True,
        )

    payload = _run_storage_query(dry_run=False)
    layout = parse_storage_layout(payload)

    def path_exists(path: str) -> bool:
        return _default_path_exists(path, dry_run=False)

    classified = classify_partitions(layout, path_exists=path_exists)
    status = validate_partition_discovery(classified)
    return DiscoveryResult(
        efi_partition=classified.efi_partition,
        msr_partition=classified.msr_partition,
        windows_partition=classified.windows_partition,
        recovery_image_partition=classified.recovery_image_partition,
        recovery_linux_partition=classified.recovery_linux_partition,
        status=status,
        dry_run=False,
    )


def main(argv: Optional[List[str]] = None) -> int:
    setup_logging()
    parser = argparse.ArgumentParser(description="Read-only partition discovery")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute read-only discovery commands (default: dry-run)",
    )
    args = parser.parse_args(argv)

    result = discover_partitions(dry_run=not args.live)

    print(result.to_json())

    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

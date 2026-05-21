"""Tests for partition_manager.discovery."""

import json

from partition_manager.discovery import (
    classify_partitions,
    discover_partitions,
    parse_storage_layout,
)
from partition_manager.models import (
    GPT_EFI_SYSTEM,
    GPT_MICROSOFT_RESERVED,
    LABEL_RECOVERY_IMAGE,
    LABEL_RECOVERY_LINUX,
    PartitionState,
)
from validation.partition_validation import validate_partition_discovery


def _layout(partitions, volumes=None):
    return {"disks": [], "partitions": partitions, "volumes": volumes or []}


def _part(disk, part, *, gpt_type=None, letter=None, size=1_000_000, access_paths=None):
    return {
        "DiskNumber": disk,
        "PartitionNumber": part,
        "Size": size,
        "GptType": gpt_type,
        "DriveLetter": letter,
        "AccessPaths": access_paths or [],
    }


def _vol(letter, fs, label=None):
    return {
        "DriveLetter": letter,
        "FileSystem": fs,
        "FileSystemLabel": label,
    }


def test_parse_storage_layout_empty():
    assert parse_storage_layout("") == {
        "disks": [],
        "partitions": [],
        "volumes": [],
    }


def test_classify_full_layout():
    paths = {
        "E:\\EFI\\Microsoft\\Boot\\bootmgfw.efi": True,
        "W:\\Windows": True,
        "W:\\Windows\\System32\\config\\SYSTEM": True,
    }

    def exists(path: str) -> bool:
        return paths.get(path, False)

    layout = _layout(
        [
            _part(2, 1, gpt_type=GPT_EFI_SYSTEM, letter="E"),
            _part(2, 2, gpt_type=GPT_MICROSOFT_RESERVED),
            _part(2, 3, letter="W"),
            _part(3, 1, letter="R", gpt_type=GPT_EFI_SYSTEM),
        ],
        [
            _vol("E", "FAT32"),
            _vol("W", "NTFS"),
            _vol("R", "NTFS", LABEL_RECOVERY_IMAGE),
        ],
    )

    result = classify_partitions(layout, path_exists=exists)
    assert result.efi_partition.state == PartitionState.FOUND.value
    assert result.efi_partition.disk_number == 2
    assert result.msr_partition.state == PartitionState.FOUND.value
    assert result.windows_partition.state == PartitionState.FOUND.value
    assert result.recovery_image_partition.label == LABEL_RECOVERY_IMAGE
    assert result.recovery_linux_partition is None
    assert validate_partition_discovery(result) == "PASS"


def test_multiple_windows_unsupported():
    paths = {
        "C:\\Windows": True,
        "C:\\Windows\\System32\\config\\SYSTEM": True,
        "D:\\Windows": True,
        "D:\\Windows\\System32\\config\\SYSTEM": True,
    }

    layout = _layout(
        [
            _part(4, 1, letter="C"),
            _part(5, 1, letter="D"),
        ],
        [_vol("C", "NTFS"), _vol("D", "NTFS")],
    )

    result = classify_partitions(layout, path_exists=paths.get)
    assert result.windows_partition.state == PartitionState.UNSUPPORTED.value
    assert validate_partition_discovery(result) == "FAIL"


def test_multiple_efi_unsupported():
    layout = _layout(
        [
            _part(7, 1, gpt_type=GPT_EFI_SYSTEM, letter="E"),
            _part(7, 2, gpt_type=GPT_EFI_SYSTEM, letter="F"),
        ],
        [_vol("E", "FAT32"), _vol("F", "FAT32")],
    )

    def exists(path: str) -> bool:
        return path.endswith("bootmgfw.efi")

    result = classify_partitions(layout, path_exists=exists)
    assert result.efi_partition.state == PartitionState.UNSUPPORTED.value


def test_missing_required_partitions():
    result = classify_partitions(_layout([]), path_exists=lambda _p: False)
    assert result.efi_partition.state == PartitionState.MISSING.value
    assert result.msr_partition.state == PartitionState.MISSING.value
    assert result.windows_partition.state == PartitionState.MISSING.value
    assert validate_partition_discovery(result) == "FAIL"


def test_recovery_linux_label():
    layout = _layout(
        [_part(9, 2, letter="L")],
        [_vol("L", "ext4", LABEL_RECOVERY_LINUX)],
    )
    result = classify_partitions(layout, path_exists=lambda _p: False)
    assert result.recovery_linux_partition.state == PartitionState.FOUND.value
    assert result.recovery_linux_partition.label == LABEL_RECOVERY_LINUX


def test_discover_partitions_dry_run_on_linux():
    result = discover_partitions(dry_run=True)
    assert result.dry_run is True
    assert result.status == "FAIL"


def test_discover_partitions_non_windows():
    result = discover_partitions(dry_run=False)
    assert result.status == "FAIL"
    assert result.efi_partition.state == PartitionState.MISSING.value


def test_json_output_shape():
    layout = _layout(
        [_part(2, 2, gpt_type=GPT_MICROSOFT_RESERVED)],
        [],
    )
    result = classify_partitions(layout, path_exists=lambda _p: False)
    payload = result.to_dict()
    assert payload["recovery_image_partition"] is None
    assert "status" in payload
    json.dumps(payload)

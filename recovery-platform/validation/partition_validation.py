"""Validation rules for partition discovery results."""

from __future__ import annotations

from partition_manager.models import DiscoveryResult, PartitionState


def validate_partition_discovery(result: DiscoveryResult) -> str:
    """
    Return PASS when required partitions are uniquely identified.

    Required: EFI, MSR, Windows (exactly one Windows OS candidate).
    Optional: recovery image / recovery linux (may be absent).
    """
    if result.dry_run:
        return "FAIL"

    required = (
        result.efi_partition,
        result.msr_partition,
        result.windows_partition,
    )
    if any(record is None for record in required):
        return "FAIL"

    for record in required:
        if record.state != PartitionState.FOUND.value:
            return "FAIL"

    return "PASS"

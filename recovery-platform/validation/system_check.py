"""Read-only system preflight checks (Windows / UEFI / GPT / BitLocker)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class SystemCheckResult:
    """Aggregated preflight check result."""

    is_windows: bool
    is_admin: bool
    boot_mode: str
    partition_style: str
    bitlocker: str
    secure_boot: str
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


class SystemProbes(Protocol):
    """Platform-specific read-only probes."""

    def is_windows(self) -> bool: ...

    def is_admin(self) -> bool: ...

    def boot_mode(self) -> str: ...

    def partition_style(self) -> str: ...

    def bitlocker_state(self) -> str: ...

    def secure_boot_state(self) -> str: ...


def compute_status(
    *,
    is_windows: bool,
    is_admin: bool,
    boot_mode: str,
    partition_style: str,
    bitlocker: str,
) -> str:
    """Return PASS only when all mandatory checks succeed."""
    if not is_windows:
        return "FAIL"
    if not is_admin:
        return "FAIL"
    if boot_mode != "UEFI":
        return "FAIL"
    if partition_style != "GPT":
        return "FAIL"
    if bitlocker == "ON":
        return "FAIL"
    return "PASS"


def run_system_check(probes: SystemProbes) -> SystemCheckResult:
    """Run all probes and aggregate the result."""
    is_windows = probes.is_windows()
    is_admin = probes.is_admin() if is_windows else False
    boot_mode = probes.boot_mode() if is_windows else "UNKNOWN"
    partition_style = probes.partition_style() if is_windows else "UNKNOWN"
    bitlocker = probes.bitlocker_state() if is_windows else "UNKNOWN"
    secure_boot = probes.secure_boot_state() if is_windows else "UNKNOWN"

    status = compute_status(
        is_windows=is_windows,
        is_admin=is_admin,
        boot_mode=boot_mode,
        partition_style=partition_style,
        bitlocker=bitlocker,
    )

    return SystemCheckResult(
        is_windows=is_windows,
        is_admin=is_admin,
        boot_mode=boot_mode,
        partition_style=partition_style,
        bitlocker=bitlocker,
        secure_boot=secure_boot,
        status=status,
    )


_MANAGE_BDE_ON = re.compile(
    r"protection\s+status\s*:\s*protection\s+on",
    re.IGNORECASE,
)
_MANAGE_BDE_ENCRYPTED = re.compile(
    r"percentage\s+encrypted\s*:\s*(?!0\.0%)([1-9]|[1-9][0-9])",
    re.IGNORECASE,
)


def parse_manage_bde_status(output: str) -> Optional[str]:
    """Parse manage-bde -status text; return ON/OFF or None if inconclusive."""
    if not output.strip():
        return None

    on = False
    blocks = re.split(r"\n\s*\n", output)
    for block in blocks:
        if _MANAGE_BDE_ON.search(block) or _MANAGE_BDE_ENCRYPTED.search(block):
            on = True
            break

    return "ON" if on else "OFF"


def parse_bitlocker_volumes_json(output: str) -> Optional[str]:
    """Parse Get-BitLockerVolume JSON output; return ON/OFF or None."""
    text = output.strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None

    volumes: List[Dict[str, Any]]
    if isinstance(payload, list):
        volumes = payload
    elif isinstance(payload, dict):
        volumes = [payload]
    else:
        return None

    for vol in volumes:
        protection = str(vol.get("ProtectionStatus", "")).lower()
        volume_status = str(vol.get("VolumeStatus", "")).lower()
        if protection in ("on", "1") or volume_status in (
            "fullyencrypted",
            "encryptioninprogress",
            "decryptioninprogress",
        ):
            return "ON"

    return "OFF" if volumes else None


def merge_bitlocker_states(*states: Optional[str]) -> str:
    """Prefer ON, then OFF, else UNKNOWN."""
    normalized = [s for s in states if s is not None]
    if any(s == "ON" for s in normalized):
        return "ON"
    if normalized and all(s == "OFF" for s in normalized):
        return "OFF"
    return "UNKNOWN"


def parse_bcdedit_firmware(output: str) -> str:
    """Detect UEFI vs LEGACY from bcdedit firmware enum output."""
    if re.search(r"Firmware\s+Boot\s+Manager", output, re.IGNORECASE):
        return "UEFI"
    return "LEGACY"


def parse_partition_style(output: str) -> str:
    """Parse Get-Disk PartitionStyle output."""
    text = output.strip().upper()
    if text == "GPT":
        return "GPT"
    if text in ("MBR", "RAW"):
        return "MBR"
    return "UNKNOWN"


def parse_secure_boot_confirm(output: str, *, returncode: int) -> str:
    """Parse Confirm-SecureBootUEFI output."""
    text = output.strip().lower()
    if returncode == 0:
        if text in ("true", "1"):
            return "ON"
        if text in ("false", "0"):
            return "OFF"
    if "not supported" in text or "unable to confirm" in text:
        return "N/A"
    return "UNKNOWN"

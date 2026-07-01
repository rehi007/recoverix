"""UEFI firmware boot entry models and bcdedit parsers."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

WINDOWS_BOOT_DESCRIPTION = "windows boot manager"
WINDOWS_BOOT_DESCRIPTION_KO = "windows 부팅 관리자"
WINDOWS_BOOT_PATH_FRAGMENT = r"efi/microsoft/boot/bootmgfw.efi"

RECOVERY_BOOT_DESCRIPTION = "recoverix boot manager"
LEGACY_RECOVERY_BOOT_DESCRIPTION = "recoveryboot"
RECOVERY_BOOT_PATH_FRAGMENT = r"efi/recoveryboot/shimx64.efi"

_SECTION_FIRMWARE_MANAGER = re.compile(r"^Firmware Boot Manager\s*$", re.IGNORECASE)
_SECTION_FIRMWARE_LOADER = re.compile(r"^Firmware Boot Loader\s*$", re.IGNORECASE)
_SECTION_FIRMWARE_MANAGER_KO = re.compile(r"^펌웨어\s+부팅\s+관리자\s*$")
_SECTION_FIRMWARE_LOADER_KO = re.compile(r"^펌웨어\s+.+$")
_SECTION_WINDOWS_BOOT_MANAGER = re.compile(r"^Windows Boot Manager\s*$", re.IGNORECASE)
_SECTION_WINDOWS_BOOT_MANAGER_KO = re.compile(r"^Windows\s+부팅\s+관리자\s*$", re.IGNORECASE)
_LINE_KV = re.compile(r"^(\S(?:.*?\S)?)\s{2,}(.*\S)\s*$")
_LINE_CONTINUATION = re.compile(r"^\s{2,}(\{.+?\})\s*$")
_IDENTIFIER = re.compile(r"^\{(.+?)\}$", re.IGNORECASE)

_KEY_ALIASES = {
    "identifier": "identifier",
    "식별자": "identifier",
    "description": "description",
    "설명": "description",
    "device": "device",
    "장치": "device",
    "path": "path",
    "경로": "path",
    "displayorder": "displayorder",
    "display order": "displayorder",
    "표시 순서": "displayorder",
    "표시순서": "displayorder",
    "bootnext": "bootnext",
    "boot next": "bootnext",
    "다음 부팅": "bootnext",
    "다음부팅": "bootnext",
}


@dataclass(frozen=True)
class BootEntry:
    """A single firmware boot loader entry from bcdedit."""

    identifier: str
    description: Optional[str] = None
    device: Optional[str] = None
    path: Optional[str] = None
    boot_label: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FirmwareAnalysisResult:
    """Aggregated firmware boot analysis."""

    windows_boot_manager: Optional[BootEntry]
    recovery_boot: Optional[BootEntry]
    boot_order: List[str] = field(default_factory=list)
    boot_next: Optional[str] = None
    entries: List[BootEntry] = field(default_factory=list)
    status: str = "FAIL"
    dry_run: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "windows_boot_manager": _entry_or_null(self.windows_boot_manager),
            "recovery_boot": _entry_or_null(self.recovery_boot),
            "boot_order": list(self.boot_order),
            "boot_next": self.boot_next,
            "entries": [entry.to_dict() for entry in self.entries],
            "status": self.status,
            "dry_run": self.dry_run,
        }

    def to_json(self, *, indent: Optional[int] = 2) -> str:
        public = {
            "windows_boot_manager": _entry_or_null(self.windows_boot_manager),
            "recovery_boot": _entry_or_null(self.recovery_boot),
            "boot_order": list(self.boot_order),
            "boot_next": self.boot_next,
            "status": self.status,
            "dry_run": self.dry_run,
        }
        return json.dumps(public, indent=indent)


def _entry_or_null(entry: Optional[BootEntry]) -> Optional[Dict[str, Any]]:
    if entry is None:
        return None
    return entry.to_dict()


def _normalize_path(value: Optional[str]) -> str:
    if not value:
        return ""
    return value.replace("\\", "/").strip().lower()


def _extract_boot_label(identifier: str) -> Optional[str]:
    match = _IDENTIFIER.match(identifier.strip())
    if not match:
        return None
    inner = match.group(1)
    if inner.lower().startswith("boot") and inner[4:].isdigit():
        return inner.lower()
    return None


def _split_sections(text: str) -> List[tuple[str, List[str]]]:
    sections: List[tuple[str, List[str]]] = []
    current_title = "preamble"
    current_lines: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _SECTION_FIRMWARE_MANAGER.match(line) or _SECTION_FIRMWARE_MANAGER_KO.match(line):
            if current_lines:
                sections.append((current_title, current_lines))
            current_title = "manager"
            current_lines = []
            continue
        if _SECTION_WINDOWS_BOOT_MANAGER.match(line) or _SECTION_WINDOWS_BOOT_MANAGER_KO.match(line):
            if current_lines:
                sections.append((current_title, current_lines))
            current_title = "loader"
            current_lines = ["description              Windows Boot Manager"]
            continue
        if _SECTION_FIRMWARE_LOADER.match(line) or (
            _SECTION_FIRMWARE_LOADER_KO.match(line)
            and not _SECTION_FIRMWARE_MANAGER_KO.match(line)
        ):
            if current_lines:
                sections.append((current_title, current_lines))
            current_title = "loader"
            current_lines = []
            continue
        if line.startswith("---"):
            continue
        current_lines.append(line)

    if current_lines:
        sections.append((current_title, current_lines))
    return sections


def _canonical_key(raw_key: str) -> str:
    key = " ".join(raw_key.strip().lower().split())
    return _KEY_ALIASES.get(key, key)


def _parse_block(lines: List[str]) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None

    for line in lines:
        if not line.strip():
            continue

        continuation = _LINE_CONTINUATION.match(line)
        if continuation and current_key:
            value = continuation.group(1)
            if current_key == "displayorder":
                data.setdefault("displayorder", []).append(value)
            continue

        match = _LINE_KV.match(line)
        if not match:
            continue

        key = _canonical_key(match.group(1))
        value = match.group(2).strip()
        current_key = key

        if key == "displayorder":
            data.setdefault("displayorder", []).append(value)
        else:
            data[key] = value

    return data


def parse_bcdedit_firmware(text: str) -> tuple[List[BootEntry], List[str], Optional[str]]:
    """
    Parse `bcdedit /enum firmware` output.

    Returns (entries, boot_order, boot_next).
    """
    entries: List[BootEntry] = []
    boot_order: List[str] = []
    boot_next: Optional[str] = None

    sections = _split_sections(text)
    for title, lines in sections:
        block = _parse_block(lines)
        if title == "manager":
            order = block.get("displayorder")
            if isinstance(order, list):
                boot_order = list(order)
            boot_next = block.get("bootnext") or boot_next
            continue

        if title != "loader":
            continue

        identifier = block.get("identifier")
        if not identifier:
            continue

        entry = BootEntry(
            identifier=identifier,
            description=block.get("description"),
            device=block.get("device"),
            path=block.get("path"),
            boot_label=_extract_boot_label(identifier),
        )
        entries.append(entry)

    return entries, boot_order, boot_next


def identify_windows_boot_manager(entries: List[BootEntry]) -> Optional[BootEntry]:
    """Find the Windows Boot Manager firmware entry."""
    matches = [entry for entry in entries if _is_windows_boot_manager(entry)]
    if not matches:
        return None
    return matches[0]


def identify_recovery_boot(entries: List[BootEntry]) -> Optional[BootEntry]:
    """Find the RecoveryBoot firmware entry."""
    matches = [entry for entry in entries if _is_recovery_boot(entry)]
    if not matches:
        return None
    return matches[0]


def _is_windows_boot_manager(entry: BootEntry) -> bool:
    """Match by EFI path and/or description (no Boot#### hardcoding)."""
    description = (entry.description or "").lower()
    path = _normalize_path(entry.path)
    path_ok = WINDOWS_BOOT_PATH_FRAGMENT in path
    desc_ok = (
        WINDOWS_BOOT_DESCRIPTION in description
        or WINDOWS_BOOT_DESCRIPTION_KO in description
    )
    return path_ok or desc_ok


def _is_recovery_boot(entry: BootEntry) -> bool:
    """Match Recoverix Boot Manager shim path and/or supported descriptions."""
    description = (entry.description or "").lower()
    path = _normalize_path(entry.path)
    path_ok = RECOVERY_BOOT_PATH_FRAGMENT in path
    desc_ok = (
        RECOVERY_BOOT_DESCRIPTION in description
        or LEGACY_RECOVERY_BOOT_DESCRIPTION in description
    )
    if path:
        return path_ok
    return desc_ok


def analyze_firmware_output(text: str, *, dry_run: bool = False) -> FirmwareAnalysisResult:
    """Parse firmware enum output and identify key boot entries."""
    entries, boot_order, boot_next = parse_bcdedit_firmware(text)
    windows = identify_windows_boot_manager(entries)
    recovery = identify_recovery_boot(entries)
    status = "PASS" if windows is not None and not dry_run else "FAIL"

    return FirmwareAnalysisResult(
        windows_boot_manager=windows,
        recovery_boot=recovery,
        boot_order=boot_order,
        boot_next=boot_next,
        entries=entries,
        status=status,
        dry_run=dry_run,
    )

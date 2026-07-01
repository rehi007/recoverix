"""Tests for boot_manager firmware parsing and analysis."""

from boot_manager.boot_entry import (
    analyze_firmware_output,
    identify_recovery_boot,
    identify_windows_boot_manager,
    parse_bcdedit_firmware,
)
from boot_manager.firmware_reader import read_firmware_boot

_SAMPLE_FIRMWARE_ENUM = """
Firmware Boot Manager
---------------------
identifier              {fwbootmgr}
displayorder            {aaaa1111-1111-1111-1111-aaaaaaaaaaaa}
                        {bbbb2222-2222-2222-2222-bbbbbbbbbbbb}
bootnext                {aaaa1111-1111-1111-1111-aaaaaaaaaaaa}
timeout                 1

Firmware Boot Loader
---------------------
identifier              {aaaa1111-1111-1111-1111-aaaaaaaaaaaa}
description             Windows Boot Manager
device                  partition=C:
path                    \\EFI\\Microsoft\\Boot\\bootmgfw.efi

Firmware Boot Loader
---------------------
identifier              {bbbb2222-2222-2222-2222-bbbbbbbbbbbb}
description             Recoverix Boot Manager
device                  partition=G:
path                    \\EFI\\RecoveryBoot\\shimx64.efi

Firmware Boot Loader
---------------------
identifier              {cccc3333-3333-3333-3333-cccccccccccc}
description             Other Linux
device                  partition=H:
path                    \\EFI\\Other\\grubx64.efi
"""


def test_parse_firmware_entries_and_order():
    entries, boot_order, boot_next = parse_bcdedit_firmware(_SAMPLE_FIRMWARE_ENUM)
    assert len(entries) == 3
    assert boot_order == [
        "{aaaa1111-1111-1111-1111-aaaaaaaaaaaa}",
        "{bbbb2222-2222-2222-2222-bbbbbbbbbbbb}",
    ]
    assert boot_next == "{aaaa1111-1111-1111-1111-aaaaaaaaaaaa}"


def test_identify_windows_boot_manager():
    entries, _, _ = parse_bcdedit_firmware(_SAMPLE_FIRMWARE_ENUM)
    windows = identify_windows_boot_manager(entries)
    assert windows is not None
    assert windows.description == "Windows Boot Manager"
    assert "bootmgfw.efi" in (windows.path or "").lower()


def test_identify_recovery_boot():
    entries, _, _ = parse_bcdedit_firmware(_SAMPLE_FIRMWARE_ENUM)
    recovery = identify_recovery_boot(entries)
    assert recovery is not None
    assert recovery.description == "Recoverix Boot Manager"
    assert "shimx64.efi" in (recovery.path or "").lower()


def test_analyze_firmware_output_pass():
    result = analyze_firmware_output(_SAMPLE_FIRMWARE_ENUM, dry_run=False)
    assert result.status == "PASS"
    assert result.windows_boot_manager is not None
    assert result.recovery_boot is not None
    assert len(result.boot_order) == 2


def test_analyze_without_recovery_still_pass():
    text = """
Firmware Boot Manager
---------------------
identifier              {fwbootmgr}
displayorder            {aaaa1111-1111-1111-1111-aaaaaaaaaaaa}

Firmware Boot Loader
---------------------
identifier              {aaaa1111-1111-1111-1111-aaaaaaaaaaaa}
description             Windows Boot Manager
path                    \\EFI\\Microsoft\\Boot\\bootmgfw.efi
"""
    result = analyze_firmware_output(text, dry_run=False)
    assert result.status == "PASS"
    assert result.recovery_boot is None


def test_parse_korean_bcdedit_firmware_output():
    text = """
펌웨어 부팅 관리자
---------------------
식별자                  {fwbootmgr}
표시 순서               {aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}
                        {bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}

펌웨어 응용 프로그램(101fffff)
---------------------
식별자                  {aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}
장치                    partition=C:
경로                    \\EFI\\Microsoft\\Boot\\bootmgfw.efi
설명                    Windows 부팅 관리자

펌웨어 응용 프로그램(101fffff)
---------------------
식별자                  {bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}
장치                    partition=C:
경로                    \\EFI\\ubuntu\\shimx64.efi
설명                    ubuntu
"""
    result = analyze_firmware_output(text, dry_run=False)
    assert result.status == "PASS"
    assert result.windows_boot_manager is not None
    assert result.windows_boot_manager.description == "Windows 부팅 관리자"
    assert result.boot_order == [
        "{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa}",
        "{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb}",
    ]


def test_parse_korean_windows_boot_manager_section_title():
    text = """
펌웨어 부팅 관리자
---------------------
식별자                  {fwbootmgr}
displayorder            {bootmgr}
                        {280d2eaa-523b-11f1-8155-bc0041178553}

Windows 부팅 관리자
---------------------
identifier              {bootmgr}
device                  partition=\\Device\\HarddiskVolume1
path                    \\EFI\\Microsoft\\Boot\\BOOTMGFW.EFI
inherit                 {globalsettings}

펌웨어 응용 프로그램(101fffff)
---------------------
identifier              {280d2eaa-523b-11f1-8155-bc0041178553}
device                  partition=\\Device\\HarddiskVolume1
path                    \\EFI\\ubuntu\\shimx64.efi
description             ubuntu
"""
    result = analyze_firmware_output(text, dry_run=False)
    assert result.status == "PASS"
    assert result.windows_boot_manager is not None
    assert result.windows_boot_manager.identifier == "{bootmgr}"
    assert result.boot_order[:2] == [
        "{bootmgr}",
        "{280d2eaa-523b-11f1-8155-bc0041178553}",
    ]


def test_analyze_missing_windows_fails():
    text = """
Firmware Boot Manager
---------------------
identifier              {fwbootmgr}
displayorder            {bbbb2222-2222-2222-2222-bbbbbbbbbbbb}

Firmware Boot Loader
---------------------
identifier              {bbbb2222-2222-2222-2222-bbbbbbbbbbbb}
description             Recoverix Boot Manager
path                    \\EFI\\RecoveryBoot\\shimx64.efi
"""
    result = analyze_firmware_output(text, dry_run=False)
    assert result.status == "FAIL"
    assert result.windows_boot_manager is None


def test_boot_label_extracted_without_hardcoding():
    text = """
Firmware Boot Loader
---------------------
identifier              {boot0007}
description             Windows Boot Manager
path                    \\EFI\\Microsoft\\Boot\\bootmgfw.efi
"""
    entries, _, _ = parse_bcdedit_firmware(text)
    assert entries[0].boot_label == "boot0007"


def test_recovery_boot_identified_by_shim_path_without_keyword_description():
    text = """
Firmware Boot Loader
---------------------
identifier              {boot0012}
description             Lenovo Diagnostics
path                    \\EFI\\RecoveryBoot\\shimx64.efi
"""
    entries, _, _ = parse_bcdedit_firmware(text)
    recovery = identify_recovery_boot(entries)
    assert recovery is not None
    assert recovery.identifier == "{boot0012}"


def test_recovery_boot_description_with_wrong_path_is_ignored():
    text = """
Windows 부팅 관리자
---------------------
identifier              {bad-copy}
path                    \\EFI\\Microsoft\\Boot\\bootmgfw.efi
description             "RecoveryBoot"
"""
    entries, _, _ = parse_bcdedit_firmware(text)
    recovery = identify_recovery_boot(entries)
    assert recovery is None


def test_read_firmware_boot_dry_run():
    result = read_firmware_boot(dry_run=True)
    assert result.dry_run is True
    assert result.status == "FAIL"


def test_json_output_shape():
    result = analyze_firmware_output(_SAMPLE_FIRMWARE_ENUM, dry_run=False)
    payload = result.to_dict()
    assert "boot_order" in payload
    assert payload["windows_boot_manager"]["identifier"].startswith("{")

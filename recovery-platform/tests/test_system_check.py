"""Tests for validation.system_check."""

from validation.system_check import (
    SystemCheckResult,
    compute_status,
    merge_bitlocker_states,
    parse_bcdedit_firmware,
    parse_bitlocker_volumes_json,
    parse_manage_bde_status,
    parse_partition_style,
    parse_secure_boot_confirm,
    run_system_check,
)


class _FakeProbes:
    def __init__(self, **kwargs):
        self._values = {
            "is_windows": True,
            "is_admin": True,
            "boot_mode": "UEFI",
            "partition_style": "GPT",
            "bitlocker_state": "OFF",
            "secure_boot_state": "ON",
            **kwargs,
        }

    def is_windows(self) -> bool:
        return self._values["is_windows"]

    def is_admin(self) -> bool:
        return self._values["is_admin"]

    def boot_mode(self) -> str:
        return self._values["boot_mode"]

    def partition_style(self) -> str:
        return self._values["partition_style"]

    def bitlocker_state(self) -> str:
        return self._values["bitlocker_state"]

    def secure_boot_state(self) -> str:
        return self._values["secure_boot_state"]


def test_run_system_check_pass():
    result = run_system_check(_FakeProbes())
    assert result == SystemCheckResult(
        is_windows=True,
        is_admin=True,
        boot_mode="UEFI",
        partition_style="GPT",
        bitlocker="OFF",
        secure_boot="ON",
        status="PASS",
    )


def test_bitlocker_on_fails():
    result = run_system_check(_FakeProbes(bitlocker_state="ON"))
    assert result.bitlocker == "ON"
    assert result.status == "FAIL"


def test_non_windows_fails():
    result = run_system_check(_FakeProbes(is_windows=False))
    assert result.is_admin is False
    assert result.status == "FAIL"


def test_parse_manage_bde_on():
    sample = """
Volume C:
    Protection Status:    Protection On
    Percentage Encrypted: 100.0%
"""
    assert parse_manage_bde_status(sample) == "ON"


def test_parse_manage_bde_off():
    sample = """
Volume C:
    Protection Status:    Protection Off
    Percentage Encrypted: 0.0%
"""
    assert parse_manage_bde_status(sample) == "OFF"


def test_parse_bitlocker_volumes_on():
    payload = '[{"VolumeStatus":"FullyEncrypted","ProtectionStatus":"On"}]'
    assert parse_bitlocker_volumes_json(payload) == "ON"


def test_parse_bitlocker_volumes_off():
    payload = '[{"VolumeStatus":"FullyDecrypted","ProtectionStatus":"Off"}]'
    assert parse_bitlocker_volumes_json(payload) == "OFF"


def test_merge_bitlocker_states():
    assert merge_bitlocker_states("OFF", "ON") == "ON"
    assert merge_bitlocker_states("OFF", None) == "OFF"
    assert merge_bitlocker_states(None, None) == "UNKNOWN"


def test_parse_bcdedit_firmware():
    assert parse_bcdedit_firmware("Firmware Boot Manager") == "UEFI"
    assert parse_bcdedit_firmware("Windows Boot Manager") == "LEGACY"


def test_parse_partition_style():
    assert parse_partition_style("GPT") == "GPT"
    assert parse_partition_style("mbr") == "MBR"


def test_parse_secure_boot_confirm():
    assert parse_secure_boot_confirm("True", returncode=0) == "ON"
    assert parse_secure_boot_confirm("False", returncode=0) == "OFF"


def test_compute_status_secure_boot_off_still_passes():
    assert (
        compute_status(
            is_windows=True,
            is_admin=True,
            boot_mode="UEFI",
            partition_style="GPT",
            bitlocker="OFF",
        )
        == "PASS"
    )

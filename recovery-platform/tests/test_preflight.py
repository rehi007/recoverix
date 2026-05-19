"""Tests for windows_agent.preflight CLI."""

import json
from unittest.mock import MagicMock, patch

import windows_agent.preflight as preflight
from validation.system_check import SystemCheckResult


def test_main_json_output(capsys):
    sample = SystemCheckResult(
        is_windows=True,
        is_admin=True,
        boot_mode="UEFI",
        partition_style="GPT",
        bitlocker="OFF",
        secure_boot="ON",
        status="PASS",
    )
    with patch.object(preflight, "run_preflight", return_value=sample):
        code = preflight.main(["--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert payload["bitlocker"] == "OFF"
    assert code == 0


def test_main_exit_code_fail():
    sample = SystemCheckResult(
        is_windows=True,
        is_admin=True,
        boot_mode="UEFI",
        partition_style="GPT",
        bitlocker="ON",
        secure_boot="ON",
        status="FAIL",
    )
    with patch.object(preflight, "run_preflight", return_value=sample):
        code = preflight.main(["--json"])

    assert code == 1


@patch("windows_agent.preflight.run_readonly")
def test_windows_probes_bitlocker_manage_bde_on(mock_run):
    manage_out = "Protection Status:    Protection On\n"
    mock_run.side_effect = [
        MagicMock(returncode=0, stdout=manage_out, stderr=""),
        MagicMock(
            returncode=0,
            stdout='[{"VolumeStatus":"FullyDecrypted","ProtectionStatus":"Off"}]',
            stderr="",
        ),
    ]

    probes = preflight.WindowsSystemProbes()
    with patch.object(sys, "platform", "win32"):
        assert probes.bitlocker_state() == "ON"

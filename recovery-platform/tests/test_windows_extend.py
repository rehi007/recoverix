"""Tests for safe Windows partition extension planning."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import partition_manager.windows_extend as win_extend
from common.command import CommandResult


def _layout() -> SimpleNamespace:
    return SimpleNamespace(
        disk_path="/dev/nvme0n1",
        windows=SimpleNamespace(path="/dev/nvme0n1p2", size=1000 * 512),
        efi=SimpleNamespace(path="/dev/nvme0n1p1"),
        recovery_image=SimpleNamespace(path="/dev/nvme0n1p4", mountpoint="/mnt/recovery"),
        volumes=[],
    )


def _result(argv, stdout: str = "", stderr: str = "", rc: int = 0) -> CommandResult:
    return CommandResult(
        argv=list(argv),
        returncode=rc,
        stdout=stdout,
        stderr=stderr,
        dry_run=False,
    )


def _sfdisk_json(*, next_start: int = 8192, p2_size: int = 1000) -> str:
    last_lba = max(20000, next_start + 2000)
    return json.dumps(
        {
            "partitiontable": {
                "label": "gpt",
                "device": "/dev/nvme0n1",
                "unit": "sectors",
                "firstlba": 34,
                "lastlba": last_lba,
                "sectorsize": 512,
                "partitions": [
                    {"node": "/dev/nvme0n1p1", "start": 34, "size": 1000},
                    {"node": "/dev/nvme0n1p2", "start": 2048, "size": p2_size},
                    {"node": "/dev/nvme0n1p3", "start": next_start, "size": 1000},
                ],
            }
        }
    )


def _make_recovery_root(tmp_path: Path) -> Path:
    image = tmp_path / win_extend.DEFAULT_IMAGE_FILES["windows"]
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"partclone-image-placeholder")
    return tmp_path


def _readonly_ok(
    next_start: int,
    *,
    p2_size: int = 1000,
    block_size: int | None = None,
    source_blocks: int = 6144,
):
    def fake(argv):
        if argv[:2] == ["partclone.info", "-L"]:
            return _result(
                argv,
                stdout=(
                    f"Device size: 0 MB = {source_blocks} Blocks\n"
                    "Space in use: 0 MB = 100 Blocks\n"
                    "Block size: 512 Byte\n"
                ),
            )
        if argv[:2] == ["sfdisk", "--json"]:
            return _result(argv, stdout=_sfdisk_json(next_start=next_start, p2_size=p2_size))
        if argv[:2] == ["sfdisk", "--dump"]:
            return _result(argv, stdout="label: gpt\n")
        if argv[:2] == ["blockdev", "--getsize64"]:
            size = block_size if block_size is not None else p2_size * 512
            return _result(argv, stdout=f"{size}\n")
        raise AssertionError(f"unexpected readonly command: {argv}")

    return fake


@patch.object(win_extend, "read_bitlocker_state", return_value="OFF")
@patch.object(win_extend, "discover_layout", return_value=(None, _layout()))
@patch.object(win_extend.shutil, "which", return_value="/usr/bin/tool")
def test_plan_windows_extend_allows_adjacent_free_space(
    _mock_which,
    _mock_layout,
    _mock_bitlocker,
    tmp_path: Path,
):
    root = _make_recovery_root(tmp_path)
    with patch.object(win_extend, "run_readonly", side_effect=_readonly_ok(next_start=200000)):
        plan = win_extend.plan_windows_partition_extend(recovery_root=root)

    expected_target_sectors = 6144 + (
        win_extend.PARTCLONE_RESTORE_SIZE_MARGIN_BYTES // 512
    )
    assert plan.status == "PLANNED"
    assert plan.can_extend is True
    assert plan.windows_partition == "/dev/nvme0n1p2"
    assert plan.available_after_bytes > win_extend.MIN_ADJACENT_FREE_BYTES
    assert plan.required_size_bytes == 6144 * 512
    assert plan.target_end_sector == 2048 + expected_target_sectors - 1
    assert "resizepart" in plan.planned_commands["extend_partition"]


@patch.object(win_extend, "read_bitlocker_state", return_value="OFF")
@patch.object(win_extend, "discover_layout", return_value=(None, _layout()))
@patch.object(win_extend.shutil, "which", return_value="/usr/bin/tool")
def test_plan_windows_extend_rejects_no_adjacent_free_space(
    _mock_which,
    _mock_layout,
    _mock_bitlocker,
    tmp_path: Path,
):
    root = _make_recovery_root(tmp_path)
    with patch.object(win_extend, "run_readonly", side_effect=_readonly_ok(next_start=3048)):
        plan = win_extend.plan_windows_partition_extend(recovery_root=root)

    assert plan.status == "REJECTED"
    assert plan.can_extend is False
    assert "No adjacent free space" in (plan.reason or "")
    assert plan.next_partition == "/dev/nvme0n1p3"


@patch.object(win_extend, "read_bitlocker_state", return_value="OFF")
@patch.object(win_extend, "discover_layout", return_value=(None, _layout()))
@patch.object(win_extend.shutil, "which", side_effect=lambda name: None if name == "udevadm" else "/usr/bin/tool")
def test_run_windows_extend_saves_gpt_backup_and_runs_resize_commands(
    _mock_which,
    _mock_layout,
    _mock_bitlocker,
    tmp_path: Path,
):
    commands = []

    def fake_command(argv, **kwargs):
        commands.append(list(argv))
        return _result(argv)

    root = _make_recovery_root(tmp_path)
    with (
        patch.object(
            win_extend,
            "run_readonly",
            side_effect=_readonly_ok(
                next_start=200000,
                p2_size=1000,
                block_size=80 * 1024**2,
            ),
        ),
        patch.object(win_extend, "run_command", side_effect=fake_command),
    ):
        payload = win_extend.run_windows_partition_extend(backup_root=root)

    assert payload["status"] == "COMPLETED"
    assert any(path.name.startswith("gpt_before_windows_extend_") for path in (tmp_path / "metadata").iterdir())
    expected_target_end = 2048 + 6144 + (
        win_extend.PARTCLONE_RESTORE_SIZE_MARGIN_BYTES // 512
    ) - 1
    assert [
        "parted",
        "-s",
        "/dev/nvme0n1",
        "unit",
        "s",
        "resizepart",
        "2",
        f"{expected_target_end}s",
    ] in commands
    assert not any(command[0] == "ntfsresize" for command in commands)


@patch.object(win_extend, "read_bitlocker_state", return_value="OFF")
@patch.object(win_extend, "discover_layout", return_value=(None, _layout()))
@patch.object(win_extend.shutil, "which", side_effect=lambda name: None if name == "udevadm" else "/usr/bin/tool")
def test_run_windows_extend_stops_when_kernel_does_not_see_new_size(
    _mock_which,
    _mock_layout,
    _mock_bitlocker,
    tmp_path: Path,
):
    commands = []

    def fake_command(argv, **kwargs):
        commands.append(list(argv))
        return _result(argv)

    root = _make_recovery_root(tmp_path)
    with (
        patch.object(
            win_extend,
            "run_readonly",
            side_effect=_readonly_ok(next_start=200000, p2_size=1000, block_size=512000),
        ),
        patch.object(win_extend, "run_command", side_effect=fake_command),
    ):
        payload = win_extend.run_windows_partition_extend(backup_root=root)

    assert payload["status"] == "PENDING_REBOOT"
    assert "has not detected the new partition size" in payload["reason"]
    expected_target_end = 2048 + 6144 + (
        win_extend.PARTCLONE_RESTORE_SIZE_MARGIN_BYTES // 512
    ) - 1
    assert [
        "parted",
        "-s",
        "/dev/nvme0n1",
        "unit",
        "s",
        "resizepart",
        "2",
        f"{expected_target_end}s",
    ] in commands
    assert not any(command[0] == "ntfsresize" for command in commands)


@patch.object(win_extend, "read_bitlocker_state", return_value="OFF")
@patch.object(win_extend, "discover_layout", return_value=(None, _layout()))
@patch.object(win_extend.shutil, "which", side_effect=lambda name: None if name == "udevadm" else "/usr/bin/tool")
def test_run_windows_extend_reports_ready_when_partition_is_large_enough(
    _mock_which,
    _mock_layout,
    _mock_bitlocker,
    tmp_path: Path,
):
    commands = []

    def fake_command(argv, **kwargs):
        commands.append(list(argv))
        return _result(argv)

    root = _make_recovery_root(tmp_path)
    with (
        patch.object(
            win_extend,
            "run_readonly",
            side_effect=_readonly_ok(next_start=250000, p2_size=200000, block_size=102400000),
        ),
        patch.object(win_extend, "run_command", side_effect=fake_command),
    ):
        payload = win_extend.run_windows_partition_extend(backup_root=root)

    assert payload["status"] == "READY"
    assert "already large enough" in payload["reason"]
    assert commands == []

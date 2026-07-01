"""Tests for backup_engine backup planning (plan-only)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from backup_engine.backup_planner import (
    _DiscoveredLayout,
    build_backup_plan,
    has_valid_backup,
    main,
)
from backup_engine.partclone_wrapper import (
    format_gpt_backup_command,
    format_partclone_ntfs_command,
    run_partclone_backup,
)
from recovery_runtime.discover import DiscoveredVolume


def _vol(path: str, label: str | None, fstype: str, size: int, mount: str | None = None):
    return DiscoveredVolume(
        name=path.split("/")[-1],
        path=path,
        label=label,
        fstype=fstype,
        size=size,
        mountpoint=mount,
        device_type="part",
    )


def _layout(mount: str | None = "/mnt/recovery") -> _DiscoveredLayout:
    return _DiscoveredLayout(
        windows=_vol("/dev/nvme0n1p3", "OS", "ntfs", 500_000_000_000),
        efi=_vol("/dev/nvme0n1p1", None, "vfat", 512_000_000),
        recovery_image=_vol(
            "/dev/nvme0n1p5",
            "RECOVERY_IMAGE",
            "ext4",
            200_000_000_000,
            mount=mount,
        ),
        disk_path="/dev/nvme0n1",
        volumes=[],
    )


def test_apply_cli_fails():
    import io
    from contextlib import redirect_stderr

    buf = io.StringIO()
    with redirect_stderr(buf):
        code = main(["--apply"])
    assert code == 2
    assert "backup_planner does not execute backup" in buf.getvalue()


def test_dry_run_requires_flag():
    try:
        main([])
    except SystemExit:
        pass
    else:
        raise AssertionError("expected SystemExit when --dry-run missing")


def test_partclone_execution_not_implemented():
    try:
        run_partclone_backup()
    except NotImplementedError as exc:
        assert "run_backup" in str(exc)
    else:
        raise AssertionError("expected NotImplementedError")


def test_bitlocker_rejected():
    plan = build_backup_plan(layout=_layout(), bitlocker="ON")
    assert plan.status == "REJECTED"
    assert plan.can_backup is False


def test_topology_rejected_multiple_windows():
    plan = build_backup_plan(
        layout=None,
        bitlocker="OFF",
        topology_reason="unsupported disk topology: multiple Windows OS partitions",
    )
    assert plan.status == "REJECTED"
    assert "unsupported disk topology" in (plan.reason or "")


def test_blocked_when_valid_backup_exists():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "images").mkdir(parents=True)
        (root / "images" / "windows_backup.pcl").write_bytes(b"data")
        (root / "images" / "efi_backup.pcl").write_bytes(b"data")
        (root / "metadata").mkdir(parents=True, exist_ok=True)
        (root / "metadata" / "gpt_backup.bin").write_bytes(b"gpt")
        (root / "hashes").mkdir(parents=True, exist_ok=True)
        (root / "hashes" / "windows_backup.sha256").write_text("deadbeef", encoding="utf-8")
        (root / "hashes" / "efi_backup.sha256").write_text("deadbeef", encoding="utf-8")
        (root / "hashes" / "gpt_backup.sha256").write_text("deadbeef", encoding="utf-8")
        (root / "hashes" / "manifest.sha256").write_text("deadbeef", encoding="utf-8")
        manifest = root / "metadata" / "recovery-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "backup_complete": True,
                    "sha256_hashes": {
                        "images/windows_backup.pcl": "deadbeef",
                        "images/efi_backup.pcl": "deadbeef",
                        "metadata/gpt_backup.bin": "deadbeef",
                    },
                }
            ),
            encoding="utf-8",
        )
        plan = build_backup_plan(layout=_layout(str(root)), bitlocker="OFF")
        assert plan.status == "BLOCKED"
        assert plan.can_backup is False


def test_mount_required_when_unmounted():
    plan = build_backup_plan(layout=_layout(mount=None), bitlocker="OFF")
    assert plan.status == "MOUNT_REQUIRED"
    assert plan.can_backup is True
    assert plan.execution_allowed is False
    assert plan.targets is not None
    assert plan.targets.mount_required is True
    assert "mounted recovery image" in (plan.reason or "")


def test_planned_includes_all_backup_commands():
    plan = build_backup_plan(layout=_layout("/mnt/recovery"), bitlocker="OFF")
    assert plan.status == "PLANNED"
    assert plan.commands is not None
    assert "sgdisk --backup" in plan.commands.gpt_backup
    assert "partclone.fat" in plan.commands.efi_backup
    assert "partclone.ntfs" in plan.commands.windows_partclone


def test_planned_steps_include_gpt_efi_windows():
    plan = build_backup_plan(layout=_layout(), bitlocker="OFF")
    steps = " ".join(plan.planned_steps).lower()
    assert "gpt" in steps
    assert "efi" in steps
    assert "windows" in steps


def test_windows_partclone_command_format():
    cmd = format_partclone_ntfs_command(
        "/dev/nvme0n1p3",
        Path("/mnt/recovery/images/windows_backup.pcl"),
    )
    assert cmd.startswith("partclone.ntfs -c -s /dev/nvme0n1p3")
    assert cmd.endswith("/mnt/recovery/images/windows_backup.pcl")


def test_gpt_backup_command_format():
    cmd = format_gpt_backup_command("/dev/nvme0n1", Path("/mnt/recovery/metadata/gpt_backup.bin"))
    assert "sgdisk --backup" in cmd
    assert "/dev/nvme0n1" in cmd


@patch("common.command.run_command")
def test_no_subprocess_execution_in_planner(mock_run):
    plan = build_backup_plan(layout=_layout("/mnt/recovery"), bitlocker="OFF")
    assert plan.status == "PLANNED"
    mock_run.assert_not_called()


def test_has_valid_backup_false():
    with tempfile.TemporaryDirectory() as tmp:
        assert has_valid_backup(Path(tmp)) is False


def test_json_output():
    plan = build_backup_plan(layout=_layout(), bitlocker="OFF")
    payload = json.loads(plan.to_json())
    assert payload["dry_run"] is True
    assert payload["execution_allowed"] is False
    assert "commands" in payload

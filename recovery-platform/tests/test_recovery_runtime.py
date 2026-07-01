"""Tests for recovery_runtime modules."""

import json
import tempfile
from pathlib import Path

from recovery_runtime.discover import (
    DiscoveredVolume,
    find_by_label,
    parse_lsblk_json,
)
from recovery_runtime.integrity import check_manifest, verify_sha256_stub
from recovery_runtime.runtime_context import MenuAvailability
from recovery_runtime.state import RuntimeState
from recovery_runtime.actions import run_backup_action, run_restore_action
from recovery_runtime.runtime_context import RuntimeContext


_SAMPLE_LSBLK = """
{
  "blockdevices": [
    {
      "name": "nvme0n1",
      "path": "/dev/nvme0n1",
      "size": 1000000000000,
      "type": "disk",
      "children": [
        {
          "name": "nvme0n1p1",
          "path": "/dev/nvme0n1p1",
          "size": 500000000000,
          "fstype": "ntfs",
          "label": "Windows",
          "type": "part"
        },
        {
          "name": "nvme0n1p2",
          "path": "/dev/nvme0n1p2",
          "size": 200000000000,
          "fstype": "ext4",
          "label": "RECOVERY_IMAGE",
          "mountpoint": "/mnt/recovery-image",
          "type": "part"
        },
        {
          "name": "nvme0n1p3",
          "path": "/dev/nvme0n1p3",
          "size": 8000000000,
          "fstype": "ext4",
          "label": "RECOVERY_LINUX",
          "type": "part"
        }
      ]
    }
  ]
}
"""


def test_parse_lsblk_finds_recovery_labels():
    volumes = parse_lsblk_json(_SAMPLE_LSBLK)
    image = find_by_label(volumes, "RECOVERY_IMAGE")
    linux = find_by_label(volumes, "RECOVERY_LINUX")
    assert image is not None
    assert image.path == "/dev/nvme0n1p2"
    assert linux is not None
    assert linux.label == "RECOVERY_LINUX"


def test_manifest_check():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "recovery" / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}", encoding="utf-8")
        report = check_manifest(root)
        assert report.manifest_found is True
        assert report.manifest_path.endswith("manifest.json")


def test_sha256_stub():
    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / "sample.bin"
        sample.write_bytes(b"recovery-runtime-test")
        report = verify_sha256_stub(sample)
        assert report.sha256_status == "stub"
        assert "SHA256 stub computed" in report.message


def test_restore_and_backup_disabled():
    from recovery_runtime.runtime_context import RuntimeContext

    ctx = RuntimeContext(
        runtime_state=RuntimeState(restore_enabled=False, backup_enabled=False),
        menu=MenuAvailability(
            backup_executable=False,
            backup_reason="disabled",
            restore_executable=False,
            restore_reason="disabled",
        ),
    )
    noop = lambda _: ""
    assert "disabled" in run_restore_action(ctx, input_func=noop).lower()
    assert "disabled" in run_backup_action(ctx, input_func=noop).lower()


def test_state_summary():
    image = DiscoveredVolume(
        name="nvme0n1p2",
        path="/dev/nvme0n1p2",
        label="RECOVERY_IMAGE",
        fstype="ext4",
        size=1,
        mountpoint="/mnt/recovery-image",
        device_type="part",
    )
    state = RuntimeState(recovery_image=image, manifest_present=True)
    text = "\n".join(state.summary_lines())
    assert "RECOVERY_IMAGE" in text
    assert "disabled" in text


def test_state_summary_skips_separator_only_last_message_lines():
    state = RuntimeState(
        last_message=(
            "============================================================\n"
            " System Status\n"
            "============================================================\n"
            " Current runtime detection and recovery readiness."
        )
    )

    text = "\n".join(state.summary_lines())

    assert "Last message   : System Status" in text
    assert "Last message   : ============================================================" not in text


def test_validation_exception_reason_includes_detail():
    reason = RuntimeContext._human_validation_reason(
        "validation_exception",
        {"error": "Permission denied: windows_backup.pcl"},
    )
    assert "validation_exception" in reason
    assert "Permission denied" in reason

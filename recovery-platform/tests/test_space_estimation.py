"""Tests for backup space estimation (used space, not full partition)."""

import unittest
from unittest.mock import MagicMock, patch

from backup_engine.backup_planner import _DiscoveredLayout
from backup_engine.space_estimation import (
    BackupSizeEstimate,
    estimate_backup_space,
    estimate_ntfs_used_bytes,
    estimate_recovery_image_free_bytes,
)
from backup_engine.run_backup import plan_backup_run
from backup_engine.write_guard import WriteGuard
from recovery_runtime.discover import DiscoveredVolume


def _layout(
    *,
    windows_size: int = 383 * 1024**3,
    recovery_size: int = 200 * 1024**3,
    recovery_mount: str | None = "/mnt/recovery",
) -> _DiscoveredLayout:
    return _DiscoveredLayout(
        windows=DiscoveredVolume(
            name="nvme0n1p3",
            path="/dev/nvme0n1p3",
            label=None,
            fstype="ntfs",
            size=windows_size,
            mountpoint=None,
            device_type="part",
        ),
        efi=DiscoveredVolume(
            name="nvme0n1p1",
            path="/dev/nvme0n1p1",
            label=None,
            fstype="vfat",
            size=512 * 1024**2,
            mountpoint=None,
            device_type="part",
        ),
        recovery_image=DiscoveredVolume(
            name="nvme0n1p5",
            path="/dev/nvme0n1p5",
            label="RECOVERY_IMAGE",
            fstype="ext4",
            size=recovery_size,
            mountpoint=recovery_mount,
            device_type="part",
        ),
        disk_path="/dev/nvme0n1",
        volumes=[],
    )


class NtfsUsedEstimationTests(unittest.TestCase):
    @patch("backup_engine.space_estimation.run_readonly")
    def test_ntfs_used_from_ntfsinfo(self, mock_ro):
        mock_ro.return_value = MagicMock(
            returncode=0,
            stdout=(
                "Volume Information\n"
                "    Volume Size: 411041792000 (383 GB)\n"
                "    Percent Used Space: 12.00%\n"
            ),
        )
        with patch("backup_engine.space_estimation.shutil.which", return_value="/usr/bin/ntfsinfo"):
            used, method, warn, details = estimate_ntfs_used_bytes(
                "/dev/nvme0n1p3",
                mountpoint=None,
                partition_size=411041792000,
            )
        self.assertEqual(method, "ntfs_used_space")
        self.assertIsNone(warn)
        self.assertEqual(used, int(411041792000 * 0.12))
        self.assertLess(used, 411041792000 // 2)
        self.assertEqual(details.get("selected_method"), "ntfs_used_space")
        argv0 = details["probes"][0]["argv"]
        self.assertNotIn("-m", argv0)

    @patch("backup_engine.space_estimation.run_readonly")
    def test_ntfs_probe_failure_refuses_partition_size_fallback(self, mock_ro):
        mock_ro.return_value = MagicMock(returncode=1, stdout="", stderr="")
        with patch("backup_engine.space_estimation.shutil.which", return_value=None):
            used, method, warn, _details = estimate_ntfs_used_bytes(
                "/dev/nvme0n1p3",
                mountpoint=None,
                partition_size=383 * 1024**3,
            )
        self.assertEqual(method, "ntfs_usage_unavailable")
        self.assertIsNotNone(warn)
        self.assertIn("refusing", warn.lower())
        self.assertEqual(used, 0)


class BackupSpaceAggregateTests(unittest.TestCase):
    @patch("backup_engine.space_estimation.estimate_recovery_image_free_bytes", return_value=30 * 1024**3)
    @patch("backup_engine.space_estimation.estimate_ntfs_used_bytes")
    @patch("backup_engine.space_estimation.estimate_efi_backup_bytes", return_value=200 * 1024**2)
    def test_insufficient_recovery_image_blocks_backup(self, _efi, mock_ntfs, _free):
        mock_ntfs.return_value = (49 * 1024**3, "ntfs_used_space", None, {"probes": []})
        est = estimate_backup_space(_layout())
        self.assertFalse(est.can_backup)
        self.assertEqual(est.reason, "insufficient recovery image space")
        self.assertEqual(est.estimated_used_bytes, 49 * 1024**3)
        self.assertEqual(est.estimation_method, "ntfs_used_space")
        self.assertLess(est.estimated_required_gb, 60)
        self.assertEqual(
            est.estimation_details["windows_required_bytes"],
            int(49 * 1024**3 * 1.20),
        )

    @patch("backup_engine.space_estimation.estimate_recovery_image_free_bytes", return_value=500 * 1024**3)
    @patch("backup_engine.space_estimation.estimate_ntfs_used_bytes")
    @patch("backup_engine.space_estimation.estimate_efi_backup_bytes", return_value=200 * 1024**2)
    def test_used_space_not_full_partition(self, _efi, mock_ntfs, _free):
        mock_ntfs.return_value = (49 * 1024**3, "ntfs_used_space", None, {"probes": []})
        est = estimate_backup_space(_layout(windows_size=383 * 1024**3))
        self.assertTrue(est.can_backup)
        self.assertLess(est.estimated_required_bytes, 383 * 1024**3)
        self.assertEqual(est.estimated_used_bytes, 49 * 1024**3)

    @patch("backup_engine.space_estimation.estimate_recovery_image_free_bytes", return_value=500 * 1024**3)
    @patch("backup_engine.space_estimation.estimate_ntfs_used_bytes")
    @patch("backup_engine.space_estimation.estimate_efi_backup_bytes", return_value=200 * 1024**2)
    def test_unavailable_ntfs_usage_blocks_backup(self, _efi, mock_ntfs, _free):
        warning = "NTFS used-space probes failed; refusing to estimate from full Windows partition size"
        mock_ntfs.return_value = (
            0,
            "ntfs_usage_unavailable",
            warning,
            {"probes": [], "selected_method": "ntfs_usage_unavailable"},
        )
        est = estimate_backup_space(_layout(windows_size=383 * 1024**3))
        self.assertFalse(est.can_backup)
        self.assertEqual(est.reason, warning)
        self.assertEqual(est.estimated_required_bytes, 200 * 1024**2 + 1024 * 1024)
        self.assertEqual(est.estimation_method, "ntfs_usage_unavailable")


class RecoveryFreeSpaceTests(unittest.TestCase):
    @patch("backup_engine.space_estimation.run_readonly")
    def test_recovery_free_from_df(self, mock_ro):
        def side_effect(argv):
            if argv and argv[0] == "df":
                return MagicMock(
                    returncode=0,
                    stdout="size used avail\n100000000000 50000000000 50000000000\n",
                    stderr="",
                    argv=list(argv),
                )
            return MagicMock(returncode=1, stdout="", stderr="", argv=list(argv))

        mock_ro.side_effect = side_effect
        free = estimate_recovery_image_free_bytes(
            device="/dev/nvme0n1p5",
            mountpoint="/mnt/recovery",
            fstype="ext4",
        )
        self.assertEqual(free, 50000000000)


class RunBackupDryRunPathTests(unittest.TestCase):
    """plan_backup_run → estimate_backup_space (no mock on estimate_backup_space)."""

    @patch("backup_engine.backup_planner.has_valid_backup", return_value=False)
    @patch("backup_engine.run_backup.read_bitlocker_state", return_value="OFF")
    @patch("backup_engine.run_backup.discover_layout")
    @patch("backup_engine.run_backup.build_disk_metadata")
    @patch("backup_engine.space_estimation.run_readonly")
    def test_dry_run_json_ntfs_used_via_run_backup(
        self,
        mock_ro,
        mock_disk,
        mock_discover,
        _bl,
        _valid,
    ):
        mock_discover.return_value = (None, _layout(windows_size=80 * 1024**3))
        mock_disk.return_value = MagicMock(
            disk_guid="{disk}",
            disk_model="M",
            disk_serial="S",
            disk_size=1,
            windows_partition_uuid="{w}",
            efi_partition_uuid="{e}",
        )

        def ro_side_effect(argv):
            cmd = argv[0] if argv else ""
            if cmd == "ntfsinfo":
                return MagicMock(
                    returncode=0,
                    stdout=(
                        "Volume Size: 411041792000 (383 GB)\n"
                        "Percent Used Space: 12.00%\n"
                    ),
                    stderr="",
                    argv=list(argv),
                )
            if cmd == "df":
                return MagicMock(
                    returncode=0,
                    stdout="size used avail\n500000000000 1000 499999999000\n",
                    stderr="",
                    argv=list(argv),
                )
            if cmd == "blkid":
                return MagicMock(returncode=0, stdout="uuid", stderr="", argv=list(argv))
            return MagicMock(returncode=1, stdout="", stderr="", argv=list(argv))

        mock_ro.side_effect = ro_side_effect
        with patch("backup_engine.space_estimation.shutil.which", return_value="/usr/bin/ntfsinfo"):
            result = plan_backup_run(WriteGuard(apply=False, confirmed=False))
        self.assertEqual(result.estimation_method, "ntfs_used_space")
        self.assertLess(result.estimated_required_gb, 80)
        data = result.to_dict()
        self.assertIn("estimation_method", data)
        self.assertIn("estimation_details", data)
        probe_argv = str(data["estimation_details"])
        self.assertNotIn("'-m'", probe_argv)


class DryRunIntegrationTests(unittest.TestCase):
    @patch("backup_engine.run_backup.read_bitlocker_state", return_value="OFF")
    @patch("backup_engine.run_backup.discover_layout")
    @patch("backup_engine.run_backup.build_disk_metadata")
    @patch("backup_engine.run_backup.estimate_backup_space")
    def test_dry_run_includes_estimation_fields(self, mock_est, mock_disk, mock_discover, _bl):
        mock_discover.return_value = (None, _layout())
        mock_disk.return_value = MagicMock(
            disk_guid="{disk}",
            disk_model="M",
            disk_serial="S",
            disk_size=1,
            windows_partition_uuid="{w}",
            efi_partition_uuid="{e}",
        )
        mock_est.return_value = BackupSizeEstimate(
            estimated_used_bytes=49 * 1024**3,
            estimated_required_bytes=50 * 1024**3,
            estimated_required_gb=50.0,
            estimation_method="ntfs_used_space",
            recovery_image_free_bytes=500 * 1024**3,
            can_backup=True,
        )
        result = plan_backup_run(WriteGuard(apply=False, confirmed=False))
        self.assertEqual(result.estimation_method, "ntfs_used_space")
        self.assertEqual(result.estimated_used_bytes, 49 * 1024**3)
        self.assertEqual(result.estimated_required_gb, 50.0)

    @patch("backup_engine.run_backup.read_bitlocker_state", return_value="OFF")
    @patch("backup_engine.run_backup.discover_layout")
    @patch("backup_engine.run_backup.build_disk_metadata")
    @patch("backup_engine.run_backup.estimate_backup_space")
    def test_dry_run_rejected_insufficient_recovery(self, mock_est, mock_disk, mock_discover, _bl):
        mock_discover.return_value = (None, _layout())
        mock_disk.return_value = MagicMock(
            disk_guid="{disk}",
            disk_model="M",
            disk_serial="S",
            disk_size=1,
            windows_partition_uuid="{w}",
            efi_partition_uuid="{e}",
        )
        mock_est.return_value = BackupSizeEstimate(
            estimated_used_bytes=49 * 1024**3,
            estimated_required_bytes=50 * 1024**3,
            estimated_required_gb=50.0,
            estimation_method="ntfs_used_space",
            recovery_image_free_bytes=10 * 1024**3,
            can_backup=False,
            reason="insufficient recovery image space",
        )
        result = plan_backup_run(WriteGuard(apply=False, confirmed=False))
        self.assertFalse(result.can_backup)
        self.assertEqual(result.reason, "insufficient recovery image space")
        self.assertEqual(result.status, "REJECTED")


if __name__ == "__main__":
    unittest.main()

"""Tests for RECOVERY_IMAGE status evaluation and UI policy (no real devices)."""

from __future__ import annotations

from pathlib import Path

from recovery_runtime.gtk_ui.image_status import (
  ImageStatus,
  UIButtonState,
  _evaluate_backup_tree,  # type: ignore[attr-defined]
  derive_ui_button_state,
  status_to_cli_json,
)


def _make_tree(root: Path, with_backup: bool) -> None:
  (root / "images").mkdir(parents=True, exist_ok=True)
  (root / "hashes").mkdir(parents=True, exist_ok=True)
  (root / "metadata").mkdir(parents=True, exist_ok=True)
  (root / "manifests").mkdir(parents=True, exist_ok=True)
  if with_backup:
    (root / "images" / "windows_backup.pcl").write_bytes(b"x")
    (root / "images" / "efi_backup.pcl").write_bytes(b"x")
    (root / "metadata" / "gpt_backup.bin").write_bytes(b"gpt")
    (root / "hashes" / "windows_backup.sha256").write_text("h", encoding="utf-8")
    (root / "hashes" / "efi_backup.sha256").write_text("h", encoding="utf-8")
    (root / "hashes" / "gpt_backup.sha256").write_text("h", encoding="utf-8")
    (root / "hashes" / "manifest.sha256").write_text("h", encoding="utf-8")
    (root / "manifests" / "recovery-manifest.json").write_text("{}", encoding="utf-8")


def test_evaluate_backup_tree_no_backup(tmp_path: Path) -> None:
  _make_tree(tmp_path, with_backup=False)
  manifest, system_img, esp_img, hashes, valid = _evaluate_backup_tree(tmp_path)
  assert manifest is False
  assert system_img is False
  assert esp_img is False
  assert hashes is False
  assert valid is False


def test_evaluate_backup_tree_with_backup(tmp_path: Path) -> None:
  _make_tree(tmp_path, with_backup=True)
  manifest, system_img, esp_img, hashes, valid = _evaluate_backup_tree(tmp_path)
  assert manifest is True
  assert system_img is True
  assert esp_img is True
  assert hashes is True
  assert valid is True


def test_ui_policy_partition_missing() -> None:
  status = ImageStatus(
    recovery_image_partition_found=False,
    device=None,
    filesystem=None,
    mount_point=None,
    mounted=False,
    manifest_exists=False,
    system_image_exists=False,
    esp_image_exists=False,
    hashes_exist=False,
    valid_backup_exists=False,
    errors=["RECOVERY_IMAGE partition not found"],
  )
  buttons = derive_ui_button_state(status, admin_mode=False)
  assert buttons.backup_sensitive is False
  assert buttons.restore_sensitive is False
  assert buttons.delete_visible is False
  assert buttons.warning


def test_ui_policy_partition_with_no_backup() -> None:
  status = ImageStatus(
    recovery_image_partition_found=True,
    device="/dev/mock",
    filesystem="ext4",
    mount_point=Path("/mnt/mock"),
    mounted=True,
    manifest_exists=False,
    system_image_exists=False,
    esp_image_exists=False,
    hashes_exist=False,
    valid_backup_exists=False,
    errors=[],
  )
  buttons = derive_ui_button_state(status, admin_mode=False)
  assert buttons.backup_sensitive is True
  assert buttons.restore_sensitive is False
  assert buttons.delete_visible is False
  assert buttons.warning is None


def test_ui_policy_partition_with_valid_backup_admin() -> None:
  status = ImageStatus(
    recovery_image_partition_found=True,
    device="/dev/mock",
    filesystem="ext4",
    mount_point=Path("/mnt/mock"),
    mounted=True,
    manifest_exists=True,
    system_image_exists=True,
    esp_image_exists=True,
    hashes_exist=True,
    valid_backup_exists=True,
    errors=[],
  )
  buttons = derive_ui_button_state(status, admin_mode=True)
  assert buttons.backup_sensitive is False
  assert buttons.restore_sensitive is True
  assert buttons.delete_visible is True
  assert buttons.delete_sensitive is True


def test_status_to_cli_json_flags() -> None:
  status = ImageStatus(
    recovery_image_partition_found=True,
    device="/dev/mock",
    filesystem="ext4",
    mount_point=Path("/mnt/mock"),
    mounted=True,
    manifest_exists=True,
    system_image_exists=True,
    esp_image_exists=True,
    hashes_exist=True,
    valid_backup_exists=True,
    errors=[],
  )
  data = status_to_cli_json(status)
  assert '"backup_available": false' in data
  assert '"restore_available": true' in data
  assert '"delete_available": true' in data


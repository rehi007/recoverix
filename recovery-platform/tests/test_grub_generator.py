"""Tests for grub.generate_grub_config."""

import tempfile
from pathlib import Path

from grub.generate_grub_config import (
    GrubConfigOptions,
    discover_windows_efi_path,
    generate_grub_config,
    normalize_efi_path,
    normalize_line_endings,
    write_grub_config,
)


def test_normalize_efi_path():
    assert normalize_efi_path(r"\EFI\Microsoft\Boot\bootmgfw.efi") == (
        "/EFI/Microsoft/Boot/bootmgfw.efi"
    )


def test_generate_default_windows_first():
    content = generate_grub_config()
    assert "set timeout=5" in content
    assert "set default=0" in content
    assert 'menuentry "Windows Boot Manager"' in content
    assert "search --file --no-floppy --set=root /EFI/Microsoft/Boot/bootmgfw.efi" in content
    assert "chainloader /EFI/Microsoft/Boot/bootmgfw.efi" in content
    assert '--hotkey=f5' in content
    assert "linux /recovery/vmlinuz quiet recovery_mode=1" in content
    assert "initrd /recovery/initrd.img" in content


def test_windows_before_recovery_menuentry():
    content = generate_grub_config()
    windows_pos = content.index('menuentry "Windows Boot Manager"')
    recovery_pos = content.index('menuentry "Recovery Linux - Restore System"')
    assert windows_pos < recovery_pos


def test_configurable_recovery_paths():
    options = GrubConfigOptions(
        recovery_kernel="/custom/vmlinuz",
        recovery_initrd="/custom/initrd.cpio",
    )
    content = generate_grub_config(options)
    assert "linux /custom/vmlinuz quiet recovery_mode=1" in content
    assert "initrd /custom/initrd.cpio" in content


def test_keystatus_optional_load():
    content = generate_grub_config()
    assert "insmod keystatus || true" in content


def test_no_crlf_line_endings_in_generated_content():
    content = generate_grub_config()
    assert "\r\n" not in content
    assert "\r" not in content
    assert content.endswith("\n")


def test_normalize_line_endings_converts_crlf():
    assert normalize_line_endings("a\r\nb\r\nc\n") == "a\nb\nc\n"


def test_search_fallback_todo_present():
    content = generate_grub_config()
    assert "TODO(fallback)" in content
    assert "search.fs_uuid" in content
    assert "Windows boot survivability" in content


def test_discover_windows_efi_path_default():
    path = discover_windows_efi_path(live=False)
    assert path == "/EFI/Microsoft/Boot/bootmgfw.efi"


def test_write_grub_config_file():
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "grub.cfg"
        write_grub_config(output)
        raw = output.read_bytes()
        assert b"\r\n" not in raw
        text = raw.decode("utf-8")
        assert "Windows Boot Manager" in text
        assert output.is_file()


def test_cli_generation(tmp_path=None):
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "efi_assets" / "grub.cfg"
        from grub.generate_grub_config import main

        code = main(["--output", str(output)])
        assert code == 0
        assert output.exists()
        assert "set timeout=5" in output.read_text(encoding="utf-8")

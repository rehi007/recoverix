"""Commercial package assembly policy tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_build_commercial_package_module():
    script = ROOT / "scripts" / "build_commercial_package.py"
    spec = importlib.util.spec_from_file_location("build_commercial_package_for_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fallback_boot_uses_hotkey_grub_config(tmp_path, monkeypatch):
    module = _load_build_commercial_package_module()
    captured: dict[str, str] = {}

    def fake_build_standalone_grub(config_text: str, output: Path) -> None:
        captured[output.relative_to(tmp_path).as_posix()] = config_text
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-grub")

    monkeypatch.setattr(module, "build_standalone_grub", fake_build_standalone_grub)

    module.copy_efi(
        tmp_path,
        {
            "uuid": "1111-2222",
            "kernel_version": "6.8.0-recoverix",
        },
    )

    assert captured["Boot/grubx64.efi"] == captured["RecoveryBoot/grubx64.efi"]
    assert captured["Boot/grubx64.efi"] != captured["RecoverixDirect/grubx64.efi"]
    assert "set default=windows_boot_manager" in captured["Boot/grubx64.efi"]
    assert "hotkey=q" in captured["Boot/grubx64.efi"]

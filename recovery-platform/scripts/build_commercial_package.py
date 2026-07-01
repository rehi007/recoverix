#!/usr/bin/env python3
"""Build Recoverix commercial installer staging package.

This creates a customer-package input tree. It does not modify partitions,
NVRAM, EFI, or the current machine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent

PRODUCT = {
    "product": "Recoverix",
    "version": "T.1.0.0",
    "publisher": "FORYOUCOM",
    "support_phone": "1544-1879",
    "support_email": "help@foryoucom.co.kr",
    "language": "ko-KR",
    "installer_file": "RecoverixSetup-T.1.0.0-x64.exe",
}

PACKAGE_NAME = "RecoverixSetup-T.1.0.0-x64"
DEFAULT_OUT = ROOT / "release" / "commercial" / PACKAGE_NAME
DEFAULT_RUNTIME = Path("/boot/recoverix")

IGNORE_PATTERNS = shutil.ignore_patterns(
    ".git",
    ".pytest_cache",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "tests",
    "integration",
    "*.log",
)

PROGRAM_PACKAGES = [
    "backup_engine",
    "boot_manager",
    "common",
    "config",
    "grub",
    "partition_manager",
    "recovery_runtime",
    "restore_engine",
    "rollback",
    "validation",
    "windows_agent",
]

CUSTOMER_NOTICE_DOCS = {
    "EULA_DRAFT_KO.md": "EULA_KO.md",
    "PRIVACY_NOTICE_KO.md": "PrivacyNotice_KO.md",
    "BACKUP_AND_PARTITION_CONSENT_KO.md": "BackupAndPartitionConsent_KO.md",
    "SMART_APP_CONTROL_NOTICE_KO.md": "SmartAppControlNotice_KO.md",
    "THIRD_PARTY_NOTICES_TEMPLATE.txt": "ThirdPartyNotices.txt",
    "SOURCE_OFFER_KO.txt": "SourceOffer_KO.txt",
}

INTERNAL_RELEASE_DOCS = [
    "README.md",
    "INSTALLER_PREFLIGHT_POLICY.md",
    "PACKAGING_LAYOUT.md",
    "TRADEMARK_AND_BRANDING_POLICY.md",
    "ROLLBACK_REMOVAL_UPDATE_POLICY.md",
    "VERSIONING_POLICY.md",
]

RUNTIME_FILES = [
    "runtime.squashfs",
    "latest_runtime_build.stamp",
    "recovery-root.uuid",
]


def copy_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        raise FileNotFoundError(src)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=IGNORE_PATTERNS)


def copy_file(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(root: Path) -> None:
    lines: list[str] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name == "checksums.txt":
            continue
        rel = path.relative_to(root).as_posix()
        lines.append(f"{sha256_file(path)}  {rel}")
    (root / "checksums.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def copy_runtime(runtime_root: Path, dst: Path) -> list[str]:
    copied: list[str] = []
    for name in RUNTIME_FILES:
        src = runtime_root / name
        if src.is_file():
            copy_file(src, dst / name)
            copied.append(name)

    kernels = sorted(runtime_root.glob("vmlinuz-*"))
    initrds = sorted(runtime_root.glob("initrd.img-*-recoverix"))
    if not kernels:
        raise FileNotFoundError(f"missing Recoverix kernel in {runtime_root}")
    if not initrds:
        raise FileNotFoundError(f"missing Recoverix initrd in {runtime_root}")
    for src in kernels + initrds:
        copy_file(src, dst / src.name)
        copied.append(src.name)

    if "runtime.squashfs" not in copied:
        raise FileNotFoundError(f"missing runtime.squashfs in {runtime_root}")
    return copied


def copy_efi(dst: Path) -> None:
    recovery_boot_src = ROOT / "boot_manager" / "assets" / "recovery_boot"
    recovery_boot_dst = dst / "RecoveryBoot"
    recoverix_direct_dst = dst / "RecoverixDirect"
    copy_tree(recovery_boot_src, recovery_boot_dst)
    copy_tree(recovery_boot_src, recoverix_direct_dst)
    for directory in (recovery_boot_dst, recoverix_direct_dst):
        placeholder = directory / "grub.cfg"
        if placeholder.exists():
            placeholder.unlink()

    templates = ROOT / "scripts" / "runtime_image" / "deploy" / "grub"
    copy_file(templates / "recoverix-esp.cfg.template", recovery_boot_dst / "grub.cfg.template")
    copy_file(templates / "recoverix-direct.cfg.template", recoverix_direct_dst / "grub.cfg.template")


def copy_status_app(dst: Path) -> None:
    package = REPO_ROOT / "windows-status-app" / "build" / "package"
    exe = package / "RecoverixStatus.exe"
    if not exe.is_file():
        raise FileNotFoundError(
            "RecoverixStatus.exe was not found. Run: make -C windows-status-app package"
        )
    status_dst = dst / "status"
    status_dst.mkdir(parents=True, exist_ok=True)
    for name in (
        "RecoverixStatus.exe",
        "create_desktop_shortcut.ps1",
        "install_status_app.cmd",
        "verify_signature.ps1",
        "sign_status_app.ps1",
    ):
        src = package / name
        if src.is_file():
            copy_file(src, status_dst / name)


def copy_program_files(dst: Path) -> None:
    for package in PROGRAM_PACKAGES:
        copy_tree(ROOT / package, dst / package)

    boot_assets = dst / "boot_manager" / "assets"
    if boot_assets.exists():
        shutil.rmtree(boot_assets)

    native_dst = dst / "native" / "nvram_writer"
    native_dst.mkdir(parents=True, exist_ok=True)
    for name in ("recoverix-nvram-writer.exe", "README.md"):
        copy_file(ROOT / "native" / "nvram_writer" / name, native_dst / name)

    copy_status_app(dst)

    licenses = dst / "licenses"
    commercial = ROOT / "docs" / "commercial"
    for source, target in CUSTOMER_NOTICE_DOCS.items():
        copy_file(commercial / source, licenses / target)

    product_manifest = {
        "product_id": "recoverix",
        "product_name": PRODUCT["product"],
        "version": PRODUCT["version"],
        "publisher": PRODUCT["publisher"],
        "support_phone": PRODUCT["support_phone"],
        "support_email": PRODUCT["support_email"],
        "language": PRODUCT["language"],
        "partition_policy": {
            "recovery_linux": "4GB",
            "recovery_image": "max(Windows used space * 0.75 + 5GB, 45GB)",
            "windows_tail_unallocated": "100MB",
        },
        "smart_app_control_limitation_notice": True,
        "public_code_signing_certificate": False,
    }
    manifest_path = dst / "config" / "product_manifest.json"
    manifest_path.write_text(
        json.dumps(product_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def copy_internal_release_docs(dst: Path) -> None:
    commercial = ROOT / "docs" / "commercial"
    target = dst / "release_docs"
    target.mkdir(parents=True, exist_ok=True)
    for name in INTERNAL_RELEASE_DOCS:
        copy_file(commercial / name, target / name)


def copy_installer_scripts(dst: Path) -> None:
    src = ROOT / "installer" / "windows"
    copy_tree(src, dst / "installer" / "windows")
    copy_file(src / "install_recoverix.cmd", dst / "install_recoverix.cmd")
    copy_file(src / "uninstall_recoverix.cmd", dst / "uninstall_recoverix.cmd")


def write_build_info(root: Path, runtime_files: list[str]) -> None:
    info = {
        **PRODUCT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_files": runtime_files,
        "partition_policy": {
            "recovery_linux": "4GB",
            "recovery_image": "max(Windows used space * 0.75 + 5GB, 45GB)",
            "windows_tail_unallocated": "100MB",
        },
        "code_signing": {
            "public_code_signing_certificate": False,
            "smart_app_control_limitation_notice": True,
        },
    }
    manifests = root / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "build-info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def make_zip(root: Path) -> Path:
    archive = root.parent / PACKAGE_NAME
    zip_path = shutil.make_archive(str(archive), "zip", root_dir=str(root))
    return Path(zip_path)


def write_nsis_script(root: Path) -> Path:
    nsis_dir = root / "installer" / "nsis"
    nsis_dir.mkdir(parents=True, exist_ok=True)
    script = nsis_dir / "RecoverixSetup.nsi"
    exe_path = root.parent / f"{PACKAGE_NAME}.exe"
    license_path = root / "payload" / "program_files" / "Recoverix" / "licenses" / "EULA_KO.md"
    content = f'''Unicode true
RequestExecutionLevel admin
Name "Recoverix {PRODUCT["version"]}"
Caption "Recoverix {PRODUCT["version"]} Setup"
OutFile "{exe_path.as_posix()}"
InstallDir "$PROGRAMFILES64\\Recoverix"
ShowInstDetails show

!include MUI2.nsh
!define MUI_ABORTWARNING
!define MUI_ICON "{(REPO_ROOT / "windows-status-app" / "resources" / "recoverix_status.ico").as_posix()}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "{license_path.as_posix()}"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "Korean"

Section "Recoverix" SEC01
  SetOutPath "$TEMP\\{PACKAGE_NAME}"
  File /r "{(root / "payload").as_posix()}"
  File /r "{(root / "installer" / "windows").as_posix()}"
  File "{(root / "install_recoverix.cmd").as_posix()}"
  File "{(root / "uninstall_recoverix.cmd").as_posix()}"
  File "{(root / "checksums.txt").as_posix()}"

  DetailPrint "Running Recoverix installer..."
  ExecWait '"$TEMP\\{PACKAGE_NAME}\\install_recoverix.cmd"' $0
  DetailPrint "Recoverix installer exit code: $0"
  ${{If}} $0 != 0
    MessageBox MB_ICONSTOP "Recoverix installation failed. Exit code: $0"
    Abort
  ${{EndIf}}

  RMDir /r "$TEMP\\{PACKAGE_NAME}"
SectionEnd
'''
    script.write_text(content, encoding="utf-8", newline="\n")
    return script


def make_nsis(root: Path) -> Path | None:
    makensis = shutil.which("makensis")
    if not makensis:
        return None
    script = write_nsis_script(root)
    proc = subprocess.run(
        [makensis, str(script)],
        cwd=str(root),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path = root / "installer" / "nsis" / "makensis.log"
    log_path.write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"makensis failed; see {log_path}")
    exe = root.parent / f"{PACKAGE_NAME}.exe"
    if not exe.is_file():
        raise FileNotFoundError(exe)
    return exe


def run_validate(root: Path) -> None:
    required = [
        root / "payload" / "program_files" / "Recoverix" / "native" / "nvram_writer" / "recoverix-nvram-writer.exe",
        root / "payload" / "program_files" / "Recoverix" / "status" / "RecoverixStatus.exe",
        root / "payload" / "runtime" / "boot" / "recoverix" / "runtime.squashfs",
        root / "payload" / "efi" / "RecoveryBoot" / "shimx64.efi",
        root / "payload" / "efi" / "RecoveryBoot" / "grub.cfg.template",
        root / "payload" / "efi" / "RecoverixDirect" / "shimx64.efi",
        root / "payload" / "efi" / "RecoverixDirect" / "grub.cfg.template",
        root / "installer" / "windows" / "install_recoverix.ps1",
        root / "manifests" / "build-info.json",
        root / "checksums.txt",
    ]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("commercial package validation failed; missing: " + ", ".join(missing))


def build(out: Path, runtime_root: Path, *, zip_output: bool, exe_output: bool) -> list[Path]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    copy_installer_scripts(out)
    copy_internal_release_docs(out)
    program_files = out / "payload" / "program_files" / "Recoverix"
    copy_program_files(program_files)
    copy_efi(out / "payload" / "efi")
    runtime_files = copy_runtime(runtime_root, out / "payload" / "runtime" / "boot" / "recoverix")
    write_build_info(out, runtime_files)
    write_checksums(out)
    run_validate(out)

    outputs: list[Path] = []
    if zip_output:
        outputs.append(make_zip(out))
    if exe_output:
        exe = make_nsis(out)
        if exe is not None:
            outputs.append(exe)
    if not outputs:
        outputs.append(out)
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Recoverix commercial package staging tree")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--no-exe", action="store_true")
    args = parser.parse_args(argv)

    try:
        results = build(
            args.output,
            args.runtime_root,
            zip_output=not args.no_zip,
            exe_output=not args.no_exe,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"build_commercial_package failed: {exc}", file=sys.stderr)
        return 1

    for result in results:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

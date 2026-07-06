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
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
VERSION_CONFIG = ROOT / "config" / "product_version.json"

DEFAULT_PRODUCT = {
    "product": "Recoverix",
    "version": "T.1.0.0",
    "windows_components_version": "T.1.0.0",
    "recovery_runtime_version": "T.1.0.0",
    "status_app_version": "T.1.0.0",
    "nvram_writer_version": "T.1.0.0",
    "publisher": "FORYOUCOM",
    "support_phone": "1544-1879",
    "support_email": "help@foryoucom.co.kr",
    "language": "ko-KR",
}

PRODUCT = dict(DEFAULT_PRODUCT)
PRODUCT["installer_file"] = "RecoverixSetup-T.1.0.0-x64.exe"
PRODUCT["package_type"] = "setup"
PRODUCT["from_version"] = ""

PACKAGE_NAME = "RecoverixSetup-T.1.0.0-x64"
DEFAULT_RUNTIME = Path("/boot/recoverix")
GIB = 1024 * 1024 * 1024
RECOVERY_LINUX_PARTITION_BYTES = 4 * GIB
RECOVERY_LINUX_IMAGE_BYTES = 768 * 1024 * 1024
GRUB_STANDALONE_MODULES = " ".join(
    [
        "part_gpt",
        "part_msdos",
        "fat",
        "ext2",
        "gzio",
        "chain",
        "search",
        "search_fs_file",
        "search_fs_uuid",
        "normal",
        "minicmd",
        "echo",
        "linux",
        "configfile",
        "test",
        "sleep",
    ]
)

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


def load_product_config() -> dict[str, str]:
    product = dict(DEFAULT_PRODUCT)
    if VERSION_CONFIG.is_file():
        loaded = json.loads(VERSION_CONFIG.read_text(encoding="utf-8"))
        for key in DEFAULT_PRODUCT:
            value = loaded.get(key)
            if isinstance(value, str) and value.strip():
                product[key] = value.strip()
    return product


def configure_product(*, version: str | None) -> None:
    global PRODUCT, PACKAGE_NAME

    product = load_product_config()
    if version:
        product["version"] = version
        product["windows_components_version"] = version
        product["recovery_runtime_version"] = version
        product["status_app_version"] = version
        product["nvram_writer_version"] = version

    product["package_type"] = "setup"
    product["from_version"] = ""
    PACKAGE_NAME = f"RecoverixSetup-{product['version']}-x64"
    product["installer_file"] = f"{PACKAGE_NAME}.exe"
    PRODUCT = product


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


def copy_utf8_bom_text_file(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    dst.write_text(text, encoding="utf-8-sig", newline="\n")
    shutil.copystat(src, dst)


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


def render_grub_template(
    template: Path,
    *,
    recovery_uuid: str,
    kernel_version: str,
    boot_timeout_sec: str,
) -> str:
    content = template.read_text(encoding="utf-8")
    replacements = {
        "@RECOVERY_UUID@": recovery_uuid,
        "@KERNEL_VERSION@": kernel_version,
        "@ESP_UUID@": "",
        "@RECOVERY_HOTKEY@": "q",
        "@BOOT_TIMEOUT_SEC@": boot_timeout_sec,
    }
    for old, new in replacements.items():
        content = content.replace(old, new)
    return content


def build_standalone_grub(config_text: str, output: Path) -> None:
    grub_mkstandalone = shutil.which("grub-mkstandalone")
    if not grub_mkstandalone:
        raise FileNotFoundError("grub-mkstandalone is required to build embedded Recoverix GRUB EFI files")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="recoverix-grub-standalone-") as temp:
        config_path = Path(temp) / "grub.cfg"
        config_path.write_text(config_text, encoding="utf-8", newline="\n")
        subprocess.run(
            [
                grub_mkstandalone,
                "-O",
                "x86_64-efi",
                "--compress=xz",
                "--install-modules",
                GRUB_STANDALONE_MODULES,
                "-o",
                str(output),
                f"boot/grub/grub.cfg={config_path}",
            ],
            check=True,
        )


def copy_efi(dst: Path, recovery_linux: dict[str, object]) -> None:
    efi_assets = ROOT / "boot_manager" / "assets" / "efi"
    recovery_boot_src = efi_assets / "RecoveryBoot"
    recoverix_direct_src = efi_assets / "RecoverixDirect"
    fallback_src = efi_assets / "Boot"
    for source in (recovery_boot_src, recoverix_direct_src, fallback_src):
        if not source.is_dir():
            raise FileNotFoundError(
                f"missing production EFI assets: {source}. "
                "Do not package placeholder boot_manager/assets/recovery_boot files."
            )

    recovery_boot_dst = dst / "RecoveryBoot"
    recoverix_direct_dst = dst / "RecoverixDirect"
    fallback_dst = dst / "Boot"
    copy_tree(recovery_boot_src, recovery_boot_dst)
    copy_tree(recoverix_direct_src, recoverix_direct_dst)
    copy_tree(fallback_src, fallback_dst)

    templates = ROOT / "scripts" / "runtime_image" / "deploy" / "grub"
    recovery_boot_template = templates / "recoverix-esp.cfg.template"
    recoverix_direct_template = templates / "recoverix-direct.cfg.template"
    copy_file(recovery_boot_template, recovery_boot_dst / "grub.cfg.template")
    copy_file(recoverix_direct_template, recoverix_direct_dst / "grub.cfg.template")

    recovery_boot_config = render_grub_template(
        recovery_boot_template,
        recovery_uuid=str(recovery_linux["uuid"]),
        kernel_version=str(recovery_linux["kernel_version"]),
        boot_timeout_sec="2",
    )
    direct_config = render_grub_template(
        recoverix_direct_template,
        recovery_uuid=str(recovery_linux["uuid"]),
        kernel_version=str(recovery_linux["kernel_version"]),
        boot_timeout_sec="0",
    )
    build_standalone_grub(recovery_boot_config, recovery_boot_dst / "grubx64.efi")
    build_standalone_grub(direct_config, recoverix_direct_dst / "grubx64.efi")
    build_standalone_grub(recovery_boot_config, fallback_dst / "grubx64.efi")


def read_recovery_uuid(runtime_root: Path) -> str:
    uuid_file = runtime_root / "recovery-root.uuid"
    if uuid_file.is_file():
        value = uuid_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    raise FileNotFoundError(f"missing recovery-root.uuid in {runtime_root}")


def kernel_version_from_runtime_files(files: list[str]) -> str:
    kernels = [name.removeprefix("vmlinuz-") for name in files if name.startswith("vmlinuz-")]
    if len(kernels) != 1:
        raise RuntimeError(f"expected one Recoverix kernel, found: {kernels}")
    return kernels[0]


def build_recovery_linux_image(runtime_root: Path, dst: Path) -> dict[str, object]:
    mke2fs = shutil.which("mke2fs")
    truncate = shutil.which("truncate")
    if not mke2fs:
        raise FileNotFoundError("mke2fs is required to build recovery-linux.ext4.img")
    if not truncate:
        raise FileNotFoundError("truncate is required to build recovery-linux.ext4.img")

    dst.mkdir(parents=True, exist_ok=True)
    image_path = dst / "recovery-linux.ext4.img"
    metadata_path = dst / "recovery-linux.json"
    recovery_uuid = read_recovery_uuid(runtime_root)

    with tempfile.TemporaryDirectory(prefix="recoverix-runtime-tree-") as temp:
        runtime_tree = Path(temp)
        runtime_boot = runtime_tree / "boot" / "recoverix"
        copied = copy_runtime(runtime_root, runtime_boot)
        kernel_version = kernel_version_from_runtime_files(copied)

        if image_path.exists():
            image_path.unlink()
        subprocess.run([truncate, "-s", str(RECOVERY_LINUX_IMAGE_BYTES), str(image_path)], check=True)
        subprocess.run(
            [
                mke2fs,
                "-q",
                "-F",
                "-t",
                "ext4",
                "-L",
                "RECOVERY_LINUX",
                "-U",
                recovery_uuid,
                "-d",
                str(runtime_tree),
                str(image_path),
            ],
            check=True,
        )

    metadata = {
        "image_file": image_path.name,
        "filesystem": "ext4",
        "label": "RECOVERY_LINUX",
        "uuid": recovery_uuid,
        "kernel_version": kernel_version,
        "image_size_bytes": image_path.stat().st_size,
        "partition_size_bytes": RECOVERY_LINUX_PARTITION_BYTES,
        "runtime_files": copied,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def copy_status_app(dst: Path) -> None:
    package = REPO_ROOT / "windows-status-app" / "build" / "package"
    exe = package / "RecoverixStatus.exe"
    if not exe.is_file():
        raise FileNotFoundError(
            "RecoverixStatus.exe was not found. Run: make -C windows-status-app package"
        )
    status_dst = dst / "StatusApp"
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
        copy_utf8_bom_text_file(commercial / source, licenses / target)

    product_manifest = {
        "product_id": "recoverix",
        "product_name": PRODUCT["product"],
        "version": PRODUCT["version"],
        "package_type": PRODUCT["package_type"],
        "from_version": PRODUCT["from_version"],
        "component_versions": {
            "windows_components": PRODUCT["windows_components_version"],
            "recovery_runtime": PRODUCT["recovery_runtime_version"],
            "status_app": PRODUCT["status_app_version"],
            "nvram_writer": PRODUCT["nvram_writer_version"],
        },
        "publisher": PRODUCT["publisher"],
        "support_phone": PRODUCT["support_phone"],
        "support_email": PRODUCT["support_email"],
        "language": PRODUCT["language"],
        "partition_policy": {
            "recovery_linux": "4GB",
            "recovery_image": "max(Windows used space * 0.75 + 10GB, 35GB)",
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
    copy_utf8_bom_text_file(src / "install_recoverix.ps1", dst / "install_recoverix.ps1")
    copy_file(src / "uninstall_recoverix.cmd", dst / "uninstall_recoverix.cmd")
    copy_utf8_bom_text_file(src / "uninstall_recoverix.ps1", dst / "uninstall_recoverix.ps1")
    for ps1 in (dst / "installer" / "windows").glob("*.ps1"):
        text = ps1.read_text(encoding="utf-8")
        ps1.write_text(text, encoding="utf-8-sig", newline="\n")


def write_build_info(root: Path, recovery_linux: dict[str, object]) -> None:
    info = {
        **PRODUCT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "recovery_linux": recovery_linux,
        "component_versions": {
            "windows_components": PRODUCT["windows_components_version"],
            "recovery_runtime": PRODUCT["recovery_runtime_version"],
            "status_app": PRODUCT["status_app_version"],
            "nvram_writer": PRODUCT["nvram_writer_version"],
        },
        "partition_policy": {
            "recovery_linux": "4GB",
            "recovery_image": "max(Windows used space * 0.75 + 10GB, 35GB)",
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
    existing_title = "Recoverix가 이미 설치되어 있습니다"
    existing_body = "현재 설치된 버전에 따라 복구 설치 또는 업데이트를 진행합니다."
    existing_action = "복구 설치/업데이트를 진행합니다."
    existing_note = "Recoverix를 제거하려면 Windows의 프로그램 제거 메뉴를 사용하세요."
    content = f'''Unicode true
RequestExecutionLevel admin
Name "Recoverix {PRODUCT["version"]} 설치"
Caption "Recoverix {PRODUCT["version"]} 설치"
OutFile "{exe_path.as_posix()}"
InstallDir "$PROGRAMFILES64\\Recoverix"
ShowInstDetails show

!include MUI2.nsh
!include LogicLib.nsh
!include nsDialogs.nsh
!define MUI_ABORTWARNING
!define MUI_ICON "{(REPO_ROOT / "windows-status-app" / "resources" / "recoverix_status.ico").as_posix()}"
!define PRODUCT_VERSION "{PRODUCT["version"]}"
!define RECOVERIX_UNINSTALL_REGKEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Recoverix"

Var ExistingRecoverixVersion
Var ExistingRecoverixAction
Var RecoverixRepairRadio
Var RecoverixCancelRadio

Function .onInit
  StrCpy $ExistingRecoverixAction "Install"
  ReadRegStr $ExistingRecoverixVersion HKLM "${{RECOVERIX_UNINSTALL_REGKEY}}" "DisplayVersion"
  ${{If}} $ExistingRecoverixVersion != ""
    ${{If}} $ExistingRecoverixVersion == "${{PRODUCT_VERSION}}"
      StrCpy $ExistingRecoverixAction "Repair"
    ${{Else}}
      StrCpy $ExistingRecoverixAction "Update"
    ${{EndIf}}
  ${{EndIf}}
FunctionEnd

Function RecoverixExistingInstallPage
  ${{If}} $ExistingRecoverixVersion == ""
    Abort
  ${{EndIf}}

  nsDialogs::Create 1018
  Pop $0
  ${{If}} $0 == error
    Abort
  ${{EndIf}}

  CreateFont $1 "$(^Font)" "11" "700"

  ${{NSD_CreateLabel}} 0 0 100% 18u "{existing_title}"
  Pop $0
  SendMessage $0 ${{WM_SETFONT}} $1 0

  ${{NSD_CreateLabel}} 0 26u 100% 62u "현재 설치된 버전: $ExistingRecoverixVersion$\\r$\\n설치 프로그램 버전: ${{PRODUCT_VERSION}}$\\r$\\n$\\r$\\n{existing_body}$\\r$\\n복구 파티션과 백업 이미지는 삭제되지 않습니다."
  Pop $0

  ${{NSD_CreateRadioButton}} 0 98u 100% 12u "{existing_action}"
  Pop $RecoverixRepairRadio
  ${{NSD_Check}} $RecoverixRepairRadio

  ${{NSD_CreateRadioButton}} 0 118u 100% 12u "설치를 종료합니다."
  Pop $RecoverixCancelRadio

  ${{NSD_CreateLabel}} 0 144u 100% 22u "{existing_note}"
  Pop $0

  nsDialogs::Show
FunctionEnd

Function RecoverixExistingInstallPageLeave
  ${{If}} $ExistingRecoverixVersion == ""
    Return
  ${{EndIf}}

  ${{NSD_GetState}} $RecoverixCancelRadio $0
  ${{If}} $0 == ${{BST_CHECKED}}
    StrCpy $ExistingRecoverixAction "Cancel"
    Quit
  ${{Else}}
    ${{If}} $ExistingRecoverixAction != "Update"
      StrCpy $ExistingRecoverixAction "Repair"
    ${{EndIf}}
  ${{EndIf}}
FunctionEnd

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "{license_path.as_posix()}"
Page custom RecoverixExistingInstallPage RecoverixExistingInstallPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "Korean"

Section "Recoverix" SEC01
  SetOutPath "$TEMP\\{PACKAGE_NAME}"
  File /r "{(root / "payload").as_posix()}"
  File /r "{(root / "installer" / "windows").as_posix()}"
  File "{(root / "install_recoverix.cmd").as_posix()}"
  File "{(root / "install_recoverix.ps1").as_posix()}"
  File "{(root / "uninstall_recoverix.cmd").as_posix()}"
  File "{(root / "uninstall_recoverix.ps1").as_posix()}"
  File "{(root / "checksums.txt").as_posix()}"

  DetailPrint "Running Recoverix installer..."
  ${{If}} $ExistingRecoverixAction == "Repair"
    ExecWait '"$TEMP\\{PACKAGE_NAME}\\install_recoverix.cmd" -ExistingInstallAction Repair' $0
  ${{ElseIf}} $ExistingRecoverixAction == "Update"
    ExecWait '"$TEMP\\{PACKAGE_NAME}\\install_recoverix.cmd" -ExistingInstallAction Update' $0
  ${{Else}}
    ExecWait '"$TEMP\\{PACKAGE_NAME}\\install_recoverix.cmd"' $0
  ${{EndIf}}
  DetailPrint "Recoverix installer exit code: $0"
  ${{If}} $0 == 1223
    MessageBox MB_ICONINFORMATION "Recoverix 설치가 취소되었습니다."
    Quit
  ${{EndIf}}
  ${{If}} $0 != 0
    MessageBox MB_ICONSTOP "Recoverix 설치에 실패했습니다. 종료 코드: $0"
    Abort
  ${{EndIf}}

  RMDir /r "$TEMP\\{PACKAGE_NAME}"
SectionEnd
'''
    script.write_text(content, encoding="utf-8-sig", newline="\n")
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
        root / "payload" / "program_files" / "Recoverix" / "StatusApp" / "RecoverixStatus.exe",
        root / "payload" / "recovery_linux" / "recovery-linux.ext4.img",
        root / "payload" / "recovery_linux" / "recovery-linux.json",
        root / "payload" / "efi" / "RecoveryBoot" / "shimx64.efi",
        root / "payload" / "efi" / "RecoveryBoot" / "grubx64.efi",
        root / "payload" / "efi" / "RecoveryBoot" / "grub.cfg.template",
        root / "payload" / "efi" / "RecoverixDirect" / "shimx64.efi",
        root / "payload" / "efi" / "RecoverixDirect" / "grubx64.efi",
        root / "payload" / "efi" / "RecoverixDirect" / "grub.cfg.template",
        root / "payload" / "efi" / "Boot" / "bootx64.efi",
        root / "payload" / "efi" / "Boot" / "grubx64.efi",
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
    recovery_linux = build_recovery_linux_image(runtime_root, out / "payload" / "recovery_linux")
    copy_efi(out / "payload" / "efi", recovery_linux)
    write_build_info(out, recovery_linux)
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
    parser.add_argument("--version", default=None, help="Target Recoverix version, for example T.1.0.1")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--no-exe", action="store_true")
    args = parser.parse_args(argv)

    configure_product(version=args.version)
    output = args.output or (ROOT / "release" / "commercial" / PACKAGE_NAME)

    try:
        results = build(
            output,
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

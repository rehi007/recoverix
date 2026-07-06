#!/usr/bin/env python3
"""Build the standalone Recoverix cleanup tool.

The generated EXE removes Windows-side Recoverix boot integration and files.
It intentionally does not delete RECOVERY_LINUX or RECOVERY_IMAGE partitions.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_VERSION = "T.1.0.0"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_nsis_script(stage: Path, version: str) -> Path:
    nsis = stage / "RecoverixCleanup.nsi"
    output = stage.parent / f"RecoverixCleanup-{version}-x64.exe"
    cleanup_script = SCRIPT_DIR / "cleanup_recoverix.ps1"
    icon = REPO_ROOT / "windows-status-app" / "resources" / "recoverix_status.ico"
    content = f'''Unicode true
RequestExecutionLevel admin
Name "Recoverix Cleanup {version}"
Caption "Recoverix Cleanup {version}"
OutFile "{output.as_posix()}"
ShowInstDetails show
Icon "{icon.as_posix()}"

!include LogicLib.nsh
!include x64.nsh

Var PowerShellExe

Function .onInit
  ${{If}} ${{RunningX64}}
    StrCpy $PowerShellExe "$WINDIR\\SysNative\\WindowsPowerShell\\v1.0\\powershell.exe"
  ${{Else}}
    StrCpy $PowerShellExe "$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe"
  ${{EndIf}}
  IfFileExists "$PowerShellExe" powershell_ok 0
  StrCpy $PowerShellExe "$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe"
powershell_ok:
  MessageBox MB_ICONEXCLAMATION|MB_YESNO "Recoverix 유지관리 정리를 시작합니다.$\\r$\\n$\\r$\\n이 도구는 Recoverix 부팅 항목과 Windows 구성요소를 정리합니다.$\\r$\\n복구 파티션과 백업 이미지는 삭제하지 않습니다.$\\r$\\n$\\r$\\n중요: 정리 완료 후 반드시 Windows를 재부팅해야 합니다.$\\r$\\n재부팅 전에는 Recoverix 재설치 또는 파티션 삭제/병합을 진행하지 마세요.$\\r$\\n$\\r$\\n계속 진행하시겠습니까?" IDYES continue
  Abort
continue:
FunctionEnd

Section "Recoverix Cleanup" SEC01
  SetOutPath "$TEMP\\RecoverixCleanup-{version}"
  File "{cleanup_script.as_posix()}"

  DetailPrint "Running Recoverix cleanup..."
  ExecWait '"$PowerShellExe" -NoProfile -ExecutionPolicy Bypass -File "$TEMP\\RecoverixCleanup-{version}\\cleanup_recoverix.ps1" -ProductVersion "{version}" -SkipInitialConfirm' $0
  DetailPrint "Recoverix cleanup exit code: $0"

  RMDir /r "$TEMP\\RecoverixCleanup-{version}"

  ${{If}} $0 == 0
    MessageBox MB_ICONQUESTION|MB_YESNO "Recoverix 유지관리 정리가 완료되었습니다.$\\r$\\n$\\r$\\n중요: 지금 Windows를 재부팅해야 합니다.$\\r$\\n재부팅 후 파티션 삭제 또는 Recoverix 재설치를 진행하세요.$\\r$\\n$\\r$\\nRECOVERY_LINUX / RECOVERY_IMAGE 파티션과 백업 이미지는 삭제하지 않았습니다.$\\r$\\n$\\r$\\n지금 재부팅하시겠습니까?" IDYES reboot_now IDNO reboot_later
reboot_now:
    Exec '"$SYSDIR\\shutdown.exe" /r /t 5 /c "Recoverix 유지관리 정리가 완료되어 Windows를 재부팅합니다."'
    MessageBox MB_ICONINFORMATION "Windows 재부팅이 예약되었습니다.$\\r$\\n$\\r$\\n로그: %TEMP%\\RecoverixCleanup-{version}.log"
    Goto done
reboot_later:
    MessageBox MB_ICONINFORMATION "재부팅이 필요합니다.$\\r$\\n$\\r$\\n재부팅 전에는 Recoverix 재설치 또는 파티션 삭제/병합을 진행하지 마세요.$\\r$\\n$\\r$\\n로그: %TEMP%\\RecoverixCleanup-{version}.log"
done:
  ${{ElseIf}} $0 == 1223
    MessageBox MB_ICONINFORMATION "Recoverix 유지관리 정리가 취소되었습니다."
  ${{Else}}
    MessageBox MB_ICONSTOP "Recoverix 유지관리 정리에 실패했습니다. 종료 코드: $0$\\r$\\n$\\r$\\n로그: %TEMP%\\RecoverixCleanup-{version}.log"
    Abort
  ${{EndIf}}
SectionEnd
'''
    nsis.write_text(content, encoding="utf-8-sig", newline="\n")
    return nsis


def build(version: str, output_root: Path) -> Path:
    makensis = shutil.which("makensis")
    if not makensis:
        raise FileNotFoundError("makensis is required to build RecoverixCleanup.exe")

    stage = output_root / f"RecoverixCleanup-{version}-x64"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    nsis = write_nsis_script(stage, version)
    proc = subprocess.run(
        [makensis, str(nsis)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    (stage / "makensis.log").write_text(proc.stdout, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"makensis failed; see {stage / 'makensis.log'}")

    exe = output_root / f"RecoverixCleanup-{version}-x64.exe"
    if not exe.is_file():
        raise FileNotFoundError(exe)
    checksum = output_root / f"RecoverixCleanup-{version}-x64.sha256"
    checksum.write_text(f"{sha256_file(exe)}  {exe.name}\n", encoding="utf-8")
    return exe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Recoverix standalone cleanup tool")
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "release" / "maintenance",
    )
    args = parser.parse_args(argv)

    try:
        exe = build(args.version, args.output_root)
    except Exception as exc:  # noqa: BLE001
        print(f"build_cleanup_tool failed: {exc}", file=sys.stderr)
        return 1

    print(exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

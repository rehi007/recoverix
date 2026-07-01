"""Native UEFI NVRAM writer integration for Windows Agent."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from common.command import run_command
from windows_agent.logging_config import append_log, repair_logger

NATIVE_WRITER_NAME = "recoverix-nvram-writer.exe"
RECOVERIX_INSTALL_ROOT = Path(r"C:\Program Files\Recoverix")


def resolve_native_writer_path(*, working_directory: Optional[Path] = None) -> Optional[Path]:
    env_path = os.environ.get("RECOVERIX_NVRAM_WRITER")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            return candidate

    roots = [RECOVERIX_INSTALL_ROOT, RECOVERIX_INSTALL_ROOT / "native" / "nvram_writer"]
    if working_directory == RECOVERIX_INSTALL_ROOT:
        roots.insert(0, working_directory)

    relatives = [
        Path("native") / "nvram_writer" / NATIVE_WRITER_NAME,
        Path("bin") / NATIVE_WRITER_NAME,
        Path(NATIVE_WRITER_NAME),
    ]
    for root in roots:
        for relative in relatives:
            candidate = root / relative
            if candidate.is_file():
                return candidate
    return None


def run_native_nvram_writer(
    *,
    working_directory: Optional[Path] = None,
    command_runner: Callable[..., object] = run_command,
) -> int:
    writer = resolve_native_writer_path(working_directory=working_directory)
    if writer is None:
        msg = "native NVRAM writer not found"
        append_log("repair.log", msg)
        repair_logger().error(msg)
        return 127

    result = command_runner(
        [str(writer)],
        dry_run=False,
        confirmed=True,
        cwd=str(working_directory) if working_directory else None,
        timeout=60,
    )
    rc = int(getattr(result, "returncode", -1))
    stdout = (getattr(result, "stdout", "") or "").strip()
    stderr = (getattr(result, "stderr", "") or "").strip()
    append_log("repair.log", f"native writer rc={rc} stdout={stdout!r} stderr={stderr!r}")
    if rc == 0:
        repair_logger().info("native NVRAM writer succeeded")
    else:
        repair_logger().error("native NVRAM writer failed rc=%s", rc)
    return rc

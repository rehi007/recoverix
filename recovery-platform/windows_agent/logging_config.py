"""File logging for RecoveryBoot Windows agent (best-effort; failure is non-fatal)."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_INSTALLED = False

LOG_MAX_BYTES = 512_000
LOG_BACKUP_COUNT = 5


def get_log_directory() -> Path:
    """Log root: %ProgramData%\\RecoveryBoot\\logs on Windows."""
    override = os.environ.get("RECOVERYBOOT_LOG_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return Path(pd) / "RecoveryBoot" / "logs"
    return Path.cwd() / ".recoveryboot_logs"


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def ensure_agent_file_logging(level: int = logging.INFO) -> None:
    """Attach rotating file handlers once (idempotent)."""
    global _INSTALLED
    if _INSTALLED:
        return
    log_dir = get_log_directory()
    _ensure_directory(log_dir)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    def attach(logger_name: str, filename: str) -> None:
        log = logging.getLogger(logger_name)
        handler = RotatingFileHandler(
            log_dir / filename,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(fmt)
        log.addHandler(handler)
        log.setLevel(level)

    attach("windows_agent", "windows_agent.log")
    attach("windows_agent.bootorder", "bootorder.log")
    attach("windows_agent.repair", "repair.log")
    attach("windows_agent.error", "error.log")
    _INSTALLED = True


def append_log(filename: str, message: str) -> None:
    """Append one line (auxiliary tail-friendly log). Logging failure != repair failure."""
    try:
        log_dir = get_log_directory()
        _ensure_directory(log_dir)
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).isoformat()
        path = log_dir / filename
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{ts} | {message}\n")
    except OSError:
        pass


def repair_logger() -> logging.Logger:
    ensure_agent_file_logging()
    return logging.getLogger("windows_agent.repair")


def agent_logger() -> logging.Logger:
    ensure_agent_file_logging()
    return logging.getLogger("windows_agent")


def error_logger() -> logging.Logger:
    ensure_agent_file_logging()
    return logging.getLogger("windows_agent.error")


def log_exception(message: str, exc: BaseException) -> None:
    try:
        error_logger().error("%s: %s", message, exc, exc_info=(type(exc), exc, exc.__traceback__))
        append_log("error.log", f"{message}: {exc}")
    except Exception:
        pass

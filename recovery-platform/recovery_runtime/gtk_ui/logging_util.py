"""Recoverix Recovery UI file logging."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

UI_LOG_PATH = Path("/var/log/recoverix-ui.log")
IMAGE_STATUS_LOG_PATH = Path("/var/log/recoverix-image-status.log")
RESTORE_PREFLIGHT_LOG_PATH = Path("/var/log/recoverix-restore-preflight.log")
RESTORE_PLAN_LOG_PATH = Path("/var/log/recoverix-restore-plan.log")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_line(message: str, *, path: Path) -> None:
    line = f"{_timestamp()} {message}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        # Best-effort logging only.
        pass


def ui_log(message: str, *, log_path: Path | None = None) -> None:
    """Append one line to the UI log (best-effort; never raises)."""
    _log_line(message, path=log_path or UI_LOG_PATH)


def image_status_log(message: str, *, log_path: Path | None = None) -> None:
    """Append one line to the image-status log (best-effort; never raises)."""
    _log_line(message, path=log_path or IMAGE_STATUS_LOG_PATH)


def restore_preflight_log(message: str, *, log_path: Path | None = None) -> None:
    """Append one line to the restore-preflight log (best-effort; never raises)."""
    _log_line(message, path=log_path or RESTORE_PREFLIGHT_LOG_PATH)


def restore_plan_log(message: str, *, log_path: Path | None = None) -> None:
    """Append one line to the restore-plan log (best-effort; never raises)."""
    _log_line(message, path=log_path or RESTORE_PLAN_LOG_PATH)

"""Runtime backup execution logging (/var/log/recoverix-backup-runtime.log)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from common.logger import get_logger

logger = get_logger(__name__)

RUNTIME_LOG_PATH = Path("/var/log/recoverix-backup-runtime.log")


def runtime_log(message: str, *, log_path: Path | None = None) -> None:
    """Append one line to recoverix-backup-runtime.log (best-effort)."""
    path = log_path or RUNTIME_LOG_PATH
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{stamp} {message}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass
    logger.info("backup-runtime: %s", message)

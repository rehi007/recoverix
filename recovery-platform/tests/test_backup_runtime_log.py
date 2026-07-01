"""Tests for /var/log/recoverix-backup-runtime.log helper."""

from __future__ import annotations

import tempfile
from pathlib import Path

from backup_engine.backup_runtime_log import runtime_log


def test_runtime_log_appends_timestamped_line():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "recoverix-backup-runtime.log"
        runtime_log("backup start", log_path=log_path)
        runtime_log("Windows backup end", log_path=log_path)
        text = log_path.read_text(encoding="utf-8")
        assert "backup start" in text
        assert "Windows backup end" in text
        assert text.count("\n") == 2

"""Shared restore path constants (no heavy imports)."""

from __future__ import annotations

from pathlib import Path

PRE_RESTORE_DIR = Path("pre_restore")
EFI_SNAPSHOT_DIR = PRE_RESTORE_DIR / "efi"
GPT_SNAPSHOT_FILE = PRE_RESTORE_DIR / "gpt_live.bin"

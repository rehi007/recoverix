"""GPT metadata rollback (single attempt, no auto retry)."""

from __future__ import annotations

import shlex
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backup_engine.backup_state import DEFAULT_IMAGE_FILES
from common.command import run_command
from common.logger import get_logger
from restore_engine.partclone_restore import format_gpt_load_command
from restore_engine.restore_paths import GPT_SNAPSHOT_FILE, PRE_RESTORE_DIR
from restore_engine.restore_state import logs_dir

logger = get_logger(__name__)


@dataclass
class GptRollbackResult:
    """Outcome of a GPT metadata rollback attempt."""

    success: bool
    status: str
    reason: Optional[str] = None
    source_backup: Optional[str] = None
    operations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _append_rollback_log(recovery_root: Path, message: str) -> None:
    try:
        log_dir = logs_dir(recovery_root)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "rollback.log"
        stamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | {message}\n")
    except OSError as exc:
        logger.debug("gpt rollback log skipped: %s", exc)


def resolve_gpt_backup_file(
    recovery_root: Path,
    *,
    prefer_live_snapshot: bool = True,
) -> Optional[Path]:
    """Prefer pre-restore live GPT snapshot, then manifest GPT backup."""
    candidates: List[Path] = []
    if prefer_live_snapshot:
        candidates.append(recovery_root / GPT_SNAPSHOT_FILE)
    candidates.append(recovery_root / DEFAULT_IMAGE_FILES["gpt"])
    candidates.append(recovery_root / PRE_RESTORE_DIR / "gpt_live.bin")
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def rollback_gpt(
    *,
    recovery_root: Path,
    disk_path: str,
    confirmed: bool,
    dry_run: bool = False,
    prefer_live_snapshot: bool = True,
) -> GptRollbackResult:
    """
    Load GPT metadata from backup via sgdisk --load-backup (single attempt).

    Automatic retry is forbidden by policy.
    """
    backup = resolve_gpt_backup_file(
        recovery_root,
        prefer_live_snapshot=prefer_live_snapshot,
    )
    if backup is None:
        reason = "GPT backup file not found for rollback"
        _append_rollback_log(recovery_root, reason)
        return GptRollbackResult(success=False, status="FAILED", reason=reason)

    command = format_gpt_load_command(backup, disk_path)
    operations = [command]
    _append_rollback_log(recovery_root, f"GPT rollback using {backup}")

    if dry_run:
        run_command(shlex.split(command), dry_run=True)
        return GptRollbackResult(
            success=True,
            status="PLANNED",
            source_backup=str(backup),
            operations=operations,
        )

    result = run_command(
        shlex.split(command),
        dry_run=False,
        confirmed=confirmed,
    )
    if result.returncode != 0:
        reason = f"GPT rollback failed: {result.stderr.strip()}"
        _append_rollback_log(recovery_root, reason)
        try:
            error_log = logs_dir(recovery_root) / "error.log"
            with error_log.open("a", encoding="utf-8") as handle:
                handle.write(f"{datetime.now(timezone.utc).isoformat()} | {reason}\n")
        except OSError:
            pass
        return GptRollbackResult(
            success=False,
            status="FAILED",
            reason=reason,
            source_backup=str(backup),
            operations=operations,
        )

    _append_rollback_log(recovery_root, "GPT rollback completed")
    return GptRollbackResult(
        success=True,
        status="COMPLETED",
        source_backup=str(backup),
        operations=operations,
    )

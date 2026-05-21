"""recovery_state.json persistence on the Recovery Image volume."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from common.logger import get_logger

logger = get_logger(__name__)

STATE_RELATIVE = Path("state/recovery_state.json")
LOGS_RELATIVE = Path("logs")


@dataclass
class RecoveryState:
    """Mutable restore lifecycle state stored on RECOVERY_IMAGE."""

    restore_in_progress: bool = False
    current_stage: str = "idle"
    rollback_required: bool = False
    last_failure_reason: Optional[str] = None
    restore_success: bool = False
    failure_count: int = 0
    recoveryboot_failure_count: int = 0
    auto_retry_allowed: bool = False
    last_successful_boot: Optional[str] = None
    last_restore_started_at: Optional[str] = None
    last_restore_finished_at: Optional[str] = None
    rollback_in_progress: bool = False
    updated_at: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RecoveryState:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        core = {key: data[key] for key in data if key in known and key != "extra"}
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(**core, extra=extra)


def state_path(recovery_root: Path, *, path: Optional[Path] = None) -> Path:
    return path or (recovery_root / STATE_RELATIVE)


def logs_dir(recovery_root: Path) -> Path:
    return recovery_root / LOGS_RELATIVE


def load_recovery_state(
    recovery_root: Path,
    *,
    path: Optional[Path] = None,
) -> RecoveryState:
    file_path = state_path(recovery_root, path=path)
    if not file_path.is_file():
        return RecoveryState()
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("invalid recovery_state.json; resetting state")
        return RecoveryState()
    if not isinstance(data, dict):
        return RecoveryState()
    return RecoveryState.from_dict(data)


def save_recovery_state(
    recovery_root: Path,
    state: RecoveryState,
    *,
    path: Optional[Path] = None,
) -> Path:
    file_path = state_path(recovery_root, path=path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now(timezone.utc).isoformat()
    file_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    logger.info("saved recovery state: %s (stage=%s)", file_path, state.current_stage)
    return file_path


def set_restore_stage(
    recovery_root: Path,
    stage: str,
    *,
    in_progress: bool,
    path: Optional[Path] = None,
) -> RecoveryState:
    state = load_recovery_state(recovery_root, path=path)
    state.current_stage = stage
    state.restore_in_progress = in_progress
    save_recovery_state(recovery_root, state, path=path)
    return state


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_restore_started(recovery_root: Path, *, path: Optional[Path] = None) -> RecoveryState:
    state = load_recovery_state(recovery_root, path=path)
    state.restore_in_progress = True
    state.current_stage = "restore_in_progress"
    state.restore_success = False
    state.rollback_required = False
    state.last_failure_reason = None
    state.last_restore_started_at = _utc_now()
    state.last_restore_finished_at = None
    save_recovery_state(recovery_root, state, path=path)
    return state


def mark_restore_success(recovery_root: Path, *, path: Optional[Path] = None) -> RecoveryState:
    state = load_recovery_state(recovery_root, path=path)
    state.restore_in_progress = False
    state.current_stage = "restore_complete"
    state.restore_success = True
    state.rollback_required = False
    state.last_failure_reason = None
    state.last_restore_finished_at = _utc_now()
    save_recovery_state(recovery_root, state, path=path)
    return state


def mark_restore_failed(
    recovery_root: Path,
    reason: str,
    *,
    stage: str,
    path: Optional[Path] = None,
) -> RecoveryState:
    state = load_recovery_state(recovery_root, path=path)
    state.restore_in_progress = False
    state.current_stage = stage
    state.restore_success = False
    state.rollback_required = True
    state.last_failure_reason = reason
    state.auto_retry_allowed = False
    state.last_restore_finished_at = _utc_now()
    save_recovery_state(recovery_root, state, path=path)
    return state


def is_restore_in_progress(state: RecoveryState) -> bool:
    return bool(state.restore_in_progress)


def is_interrupted_restore(
    state: RecoveryState,
    *,
    stale_seconds: int = 3600,
) -> bool:
    """Detect restore_in_progress without a recent finish timestamp."""
    if not state.restore_in_progress:
        return False
    if not state.last_restore_started_at:
        return True
    if state.last_restore_finished_at:
        return False
    try:
        started = datetime.fromisoformat(state.last_restore_started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - started
        return age.total_seconds() > stale_seconds
    except ValueError:
        return True

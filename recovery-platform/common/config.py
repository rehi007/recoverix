"""Minimal configuration loading."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class PlatformConfig:
    """Runtime settings for recovery operations."""

    dry_run: bool = True
    log_file: Optional[Path] = None
    log_level: str = "INFO"
    extra: Dict[str, Any] = field(default_factory=dict)


def load_config(path: Optional[Path] = None) -> PlatformConfig:
    """Load config from JSON file or return safe defaults (dry_run=True)."""
    if path is None or not path.exists():
        return PlatformConfig()

    with path.open(encoding="utf-8") as fh:
        raw: Dict[str, Any] = json.load(fh)

    log_file = raw.get("log_file")
    return PlatformConfig(
        dry_run=bool(raw.get("dry_run", True)),
        log_file=Path(log_file) if log_file else None,
        log_level=str(raw.get("log_level", "INFO")),
        extra={k: v for k, v in raw.items() if k not in ("dry_run", "log_file", "log_level")},
    )

"""Pre/post operation validation helpers.

Keep package initialization lightweight so Windows-side preflight imports do not
load restore-image validation and backup runtime dependencies unless requested.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "RestoreValidationResult",
    "SystemCheckResult",
    "run_system_check",
    "validate_partition_discovery",
    "validate_restore",
]


def __getattr__(name: str) -> Any:
    if name in {"RestoreValidationResult", "validate_restore"}:
        from .image_validation import RestoreValidationResult, validate_restore

        return {
            "RestoreValidationResult": RestoreValidationResult,
            "validate_restore": validate_restore,
        }[name]
    if name == "validate_partition_discovery":
        from .partition_validation import validate_partition_discovery

        return validate_partition_discovery
    if name in {"SystemCheckResult", "run_system_check"}:
        from .system_check import SystemCheckResult, run_system_check

        return {
            "SystemCheckResult": SystemCheckResult,
            "run_system_check": run_system_check,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

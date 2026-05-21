"""Linux recovery runtime orchestration."""

from __future__ import annotations

from typing import Any

__all__ = ["DiscoveredVolume", "RuntimeState", "discover_recovery_volumes"]


def __getattr__(name: str) -> Any:
    if name == "DiscoveredVolume":
        from .discover import DiscoveredVolume

        return DiscoveredVolume
    if name == "discover_recovery_volumes":
        from .discover import discover_recovery_volumes

        return discover_recovery_volumes
    if name == "RuntimeState":
        from .state import RuntimeState

        return RuntimeState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

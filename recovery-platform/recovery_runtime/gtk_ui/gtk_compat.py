"""GTK3 compatibility helpers for Recoverix Runtime (minimal Ubuntu images)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


def gtk_label_set_line_spacing(label: Gtk.Label, spacing: int) -> None:
    """Set label line spacing when supported (GTK 4+); no-op on GTK 3."""
    setter = getattr(label, "set_line_spacing", None)
    if callable(setter):
        setter(spacing)


def gtk_version_summary() -> str:
    """Best-effort GTK version string for logs and self-tests."""
    parts: list[str] = []
    for name in ("get_major_version", "get_minor_version", "get_micro_version"):
        fn = getattr(Gtk, name, None)
        if callable(fn):
            parts.append(str(fn()))
    if parts:
        return ".".join(parts)
    return "unknown"

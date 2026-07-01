"""Tests for GTK3 compatibility helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from recovery_runtime.gtk_ui.gtk_compat import gtk_label_set_line_spacing


def test_label_set_line_spacing_noop_when_missing():
    label = MagicMock(spec=[])  # no set_line_spacing
    gtk_label_set_line_spacing(label, 6)
    assert not hasattr(label, "set_line_spacing")


def test_label_set_line_spacing_calls_when_present():
    label = MagicMock()
    gtk_label_set_line_spacing(label, 4)
    label.set_line_spacing.assert_called_once_with(4)

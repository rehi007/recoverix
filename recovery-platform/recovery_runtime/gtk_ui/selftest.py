"""GTK UI smoke test (window + dialog creation; no main loop)."""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from recovery_runtime.gtk_ui.gtk_compat import gtk_label_set_line_spacing, gtk_version_summary


def run_ui_selftest() -> int:
    """Verify Gtk init, label compat, main window, and message dialog."""
    if Gtk.init_check(None) is None:
        print("[FAIL] Gtk.init_check returned None")
        return 1

    print(f"[PASS] Gtk.init_check OK (version {gtk_version_summary()})")

    probe = Gtk.Label(label="line\nspacing\nprobe")
    try:
        gtk_label_set_line_spacing(probe, 4)
        print("[PASS] gtk_label_set_line_spacing (no AttributeError)")
    except AttributeError as exc:
        print(f"[FAIL] gtk_label_set_line_spacing: {exc}")
        return 1

    os.environ.setdefault("RECOVERIX_UI_MOCK", "1")
    from recovery_runtime.gtk_ui.window import RecoveryWindow

    with patch.object(Gtk, "main_quit"):
        window = RecoveryWindow()
        mapped = {"value": False}

        loop = GLib.MainLoop()

        def _on_map_event(_widget, _event) -> bool:
            mapped["value"] = True
            if loop.is_running():
                loop.quit()
            return False

        window.connect("map-event", _on_map_event)

        print("[PASS] RecoveryWindow created")

        print("[SELFTEST] calling window.show_all()")
        window.show_all()
        print("[SELFTEST] calling window.present()")
        window.present()

        visible = False
        try:
            visible = bool(window.get_visible())
        except Exception:  # noqa: BLE001
            visible = False

        print(f"[SELFTEST] window.get_visible() -> {visible}")

        GLib.timeout_add(2500, loop.quit)
        loop.run()

        if not visible:
            print("[FAIL] window.get_visible() is False after show_all/present")
            window.destroy()
            return 1

        if not mapped["value"]:
            print("[FAIL] window map-event did not fire within timeout")
            window.destroy()
            return 1

        print("[PASS] window show_all/present mapped")
        window.destroy()

    dialog = Gtk.MessageDialog(
        transient_for=None,
        flags=0,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text="Recoverix UI self-test",
    )
    dialog.format_secondary_text("Dialog creation smoke test.")
    dialog.destroy()
    print("[PASS] Gtk.MessageDialog created and destroyed")

    print("[PASS] recoverix-ui-self-test complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    _ = argv
    return run_ui_selftest()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

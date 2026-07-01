"""Recovery Runtime TUI menu (keyboard navigation, no desktop GUI)."""

from __future__ import annotations

import shutil
import sys
import time
import termios
import textwrap
import tty
from typing import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from backup_engine.admin_backup import (
    current_backup_type,
)
from recovery_runtime.actions import (
    boot_recovery_status_action,
    clear_restore_failure_lock_action,
    delete_backup_action,
    reboot_to_windows_action,
    run_backup_action,
    run_restore_action,
    show_logs_action,
    show_status_action,
    windows_partition_extend_action,
)
from recovery_runtime.runtime_context import RuntimeContext
from recovery_runtime.ui_helpers import LOG_MENU_RETURN, default_input, flush_tty_input

InputFunc = Callable[[str], str]
_BOOTSTRAP_LOG_PATH = Path("/tmp/recovery-runtime-bootstrap.log")
_HEADER_WIDTH = 60
_CLEAR_SCREEN = "\033[H\033[2J\033[3J"
ADMIN_HOTKEY = "\x01"  # Ctrl+A
ADMIN_MODE_CHOICE = "__admin_mode__"


def _append_bootstrap_log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        with _BOOTSTRAP_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | {message}\n")
    except OSError:
        pass


def _reset_screen() -> None:
    try:
        sys.stdout.write(_CLEAR_SCREEN)
        sys.stdout.flush()
    except Exception:
        pass

USER_MENU_ITEMS = (
    ("1", "System Status", "status"),
    ("2", "Create Recovery Backup", "backup"),
    ("3", "Restore System", "restore"),
    ("4", "Reboot to Windows", "reboot_windows"),
)

ADMIN_MENU_ITEMS = (
    ("1", "System Status", "status"),
    ("2", "Create Recovery Backup", "backup"),
    ("3", "Restore System", "restore"),
    ("4", "Delete Backup", "delete_backup"),
    ("5", "Diagnostics Logs", "logs"),
    ("6", "Restore Partition Preparation", "windows_partition_extend"),
    ("7", "Clear Restore Failure Lock", "clear_restore_failure_lock"),
    ("8", "Boot/Recovery Status", "boot_recovery_status"),
    ("9", "Reboot to Windows", "reboot_windows"),
    ("0", "Return to Standard Mode", "exit_admin"),
)


def menu_items_for_mode(admin_mode: bool) -> tuple[tuple[str, str, str], ...]:
    return ADMIN_MENU_ITEMS if admin_mode else USER_MENU_ITEMS


def render_header(
    ctx: RuntimeContext,
    *,
    max_summary_lines: int | None = None,
    admin_mode: bool = False,
) -> None:
    _append_bootstrap_log("menu: render_header")
    term_width = shutil.get_terminal_size(fallback=(80, 24)).columns
    content_width = max(40, min(_HEADER_WIDTH, term_width - 4))
    rule = "=" * content_width
    print(rule)
    print(" Recoverix Recovery Menu")
    if admin_mode:
        print(" Mode: Administrator")
    print(rule)
    lines = ctx.runtime_state.summary_lines()
    if max_summary_lines is not None and len(lines) > max_summary_lines:
        keep = max(1, max_summary_lines - 1)
        lines = lines[:keep] + ["..."]
    for line in lines:
        wrapped = textwrap.wrap(
            line,
            width=content_width - 2,
            subsequent_indent="    ",
            break_long_words=False,
            break_on_hyphens=False,
        )
        if not wrapped:
            print("  ")
            continue
        for chunk in wrapped:
            print(f"  {chunk}")
    print(rule)


def _existing_backup_suffix(ctx: RuntimeContext, *, admin_mode: bool) -> str:
    backup_type = current_backup_type(ctx.recovery_root)
    if backup_type:
        if backup_type != "standard":
            return "unsupported backup image detected"
        return "valid backup already exists"
    return "valid backup already exists"


def _menu_suffix(ctx: RuntimeContext, feature: str, *, admin_mode: bool = False) -> str:
    menu = ctx.menu
    if feature == "backup":
        if not menu.backup_visible:
            return " [hidden]"
        if not menu.backup_executable:
            reason = menu.backup_reason or "disabled"
            if "unsupported backup" in reason.lower():
                return f" [disabled: {reason}]"
            if ctx.valid_backup or reason.lower() == "valid backup already exists":
                return f" [disabled: {_existing_backup_suffix(ctx, admin_mode=admin_mode)}]"
            return f" [disabled: {reason}]"
        return ""
    if feature == "restore":
        if not menu.restore_visible:
            return " [hidden]"
        if not menu.restore_executable:
            return f" [disabled: {menu.restore_reason or 'disabled'}]"
        return ""
    if feature == "delete_backup":
        if not menu.delete_visible:
            return " [hidden]"
        if not menu.delete_executable:
            return f" [disabled: {menu.delete_reason or 'disabled'}]"
        return ""
    return ""


def render_menu(ctx: RuntimeContext, *, admin_mode: bool = False) -> None:
    # Keep tty1 readable even if the menu loop refreshes multiple times.
    _reset_screen()
    menu_items = menu_items_for_mode(admin_mode)
    term_height = shutil.get_terminal_size(fallback=(80, 24)).lines
    fixed_rows = 4 + (1 if admin_mode else 0) + 1 + len(menu_items) + 1 + 1 + 1 + 1
    max_summary_lines = max(4, term_height - fixed_rows)
    render_header(ctx, max_summary_lines=max_summary_lines, admin_mode=admin_mode)
    print()
    for key, label, feature in menu_items:
        suffix = _menu_suffix(ctx, feature, admin_mode=admin_mode)
        print(f"  {key}. {label}{suffix}")
    print()
    print("  Press one number key to continue.")
    print()
    sys.stdout.flush()


def read_choice(
    *,
    input_func: InputFunc = default_input,
    prompt: str = "",
    accept_enter: bool = False,
    valid_choices: set[str] | None = None,
    cancel_choice: str = "6",
) -> str:
    raw_choices = set(valid_choices or set())
    normalized_choices = {choice.lower() for choice in raw_choices}
    if sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            if prompt:
                sys.stdout.write(prompt)
                sys.stdout.flush()
            new = termios.tcgetattr(fd)
            new[3] &= ~(termios.ECHO | termios.ICANON)
            new[6][termios.VMIN] = 1
            new[6][termios.VTIME] = 0
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            while True:
                raw = sys.stdin.buffer.read(1)
                if not raw:
                    break
                char = raw.decode("utf-8", errors="ignore")
                if char in ("\n", "\r"):
                    if accept_enter:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        return ""
                    continue
                if char == "\x03":
                    sys.stdout.write("^C\n")
                    sys.stdout.flush()
                    return cancel_choice
                if accept_enter:
                    continue
                if char == ADMIN_HOTKEY and ADMIN_HOTKEY in raw_choices:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return ADMIN_MODE_CHOICE
                choice = char.lower()
                if normalized_choices and choice not in normalized_choices:
                    continue
                sys.stdout.write(char + "\n")
                sys.stdout.flush()
                return choice if normalized_choices else char.strip()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    for tty_path in ("/dev/tty", "/dev/tty1"):
        try:
            with open(tty_path, "r+b", buffering=0) as handle:
                fd = handle.fileno()
                old = termios.tcgetattr(fd)
                try:
                    if prompt:
                        handle.write(prompt.encode("utf-8", errors="replace"))
                    new = termios.tcgetattr(fd)
                    new[3] &= ~(termios.ECHO | termios.ICANON)
                    new[6][termios.VMIN] = 1
                    new[6][termios.VTIME] = 0
                    termios.tcflush(fd, termios.TCIFLUSH)
                    termios.tcsetattr(fd, termios.TCSADRAIN, new)
                    while True:
                        raw = handle.read(1)
                        if not raw:
                            break
                        char = raw.decode("utf-8", errors="ignore")
                        if char in ("\n", "\r"):
                            if accept_enter:
                                handle.write(b"\n")
                                return ""
                            continue
                        if char == "\x03":
                            handle.write(b"^C\n")
                            return cancel_choice
                        if accept_enter:
                            continue
                        if char == ADMIN_HOTKEY and ADMIN_HOTKEY in raw_choices:
                            handle.write(b"\n")
                            return ADMIN_MODE_CHOICE
                        choice = char.lower()
                        if normalized_choices and choice not in normalized_choices:
                            continue
                        handle.write(raw + b"\n")
                        return choice if normalized_choices else char.strip()
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            continue

    while True:
        try:
            value = input_func("Select> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return cancel_choice
        if accept_enter and value == "":
            return ""
        if accept_enter:
            continue
        if value == ADMIN_HOTKEY and ADMIN_HOTKEY in raw_choices:
            return ADMIN_MODE_CHOICE
        if not normalized_choices:
            return value
        choice = value.lower()
        if choice in normalized_choices:
            return choice


def run_menu_loop(
    ctx: RuntimeContext,
    *,
    input_func: InputFunc = default_input,
) -> int:
    """Run interactive menu until exit. Exceptions must not crash the runtime."""
    loop_started = perf_counter()
    _append_bootstrap_log("menu: loop enter")
    admin_mode = False
    first_render = True
    first_render_logged = False
    first_render_settle_redraw = True

    while True:
        try:
            if first_render:
                time.sleep(0.15)
                flush_tty_input()
                _reset_screen()
                _append_bootstrap_log("menu: using prebuilt context")
                first_render = False
            else:
                ctx.refresh()
                _append_bootstrap_log("menu: ctx.refresh ok")
            menu_items = menu_items_for_mode(admin_mode)
            key_to_feature = {key: feature for key, _label, feature in menu_items}
            valid_menu_keys = set(key_to_feature)
            valid_menu_keys.add(ADMIN_HOTKEY)
            reboot_choice = next(
                (key for key, _label, feature in menu_items if feature == "reboot_windows"),
                "4",
            )
            render_menu(ctx, admin_mode=admin_mode)
            _append_bootstrap_log("menu: render_menu ok")
            if first_render_settle_redraw:
                # Some late tty1/systemd output can still land immediately after
                # the first draw. Redraw once after a short settle window so the
                # initial menu matches later clean refreshes.
                time.sleep(0.25)
                render_menu(ctx, admin_mode=admin_mode)
                _append_bootstrap_log("menu: settle redraw ok")
                first_render_settle_redraw = False
            if not first_render_logged:
                _append_bootstrap_log(
                    f"menu: first_render total {(perf_counter() - loop_started) * 1000.0:.1f}ms"
                )
                first_render_logged = True
            sys.stdout.flush()
            sys.stdout.write("Select> ")
            sys.stdout.flush()
            choice = read_choice(
                input_func=input_func,
                prompt="",
                valid_choices=valid_menu_keys,
                cancel_choice=reboot_choice,
            )
            _append_bootstrap_log(f"menu: read_choice={choice!r}")
            if choice == "":
                # Avoid flooding tty1 when input backend yields EOF/empty reads.
                time.sleep(0.2)
                continue
            if choice == ADMIN_MODE_CHOICE:
                admin_mode = True
                ctx.runtime_state.last_message = "Administrator mode enabled."
                continue
            feature = key_to_feature.get(choice)
            if feature is None:
                ctx.runtime_state.last_message = f"Invalid selection: {choice!r}"
                continue
            if feature == "exit_admin":
                admin_mode = False
                ctx.runtime_state.last_message = (
                    "Administrator mode exited.\nReturning to standard mode."
                )
                continue
            if feature == "status":
                _reset_screen()
                message = show_status_action(ctx)
            elif feature == "backup":
                _reset_screen()
                message = run_backup_action(ctx, input_func=input_func)
            elif feature == "restore":
                _reset_screen()
                message = run_restore_action(ctx, input_func=input_func, admin_mode=admin_mode)
            elif feature == "delete_backup":
                _reset_screen()
                message = delete_backup_action(ctx, input_func=input_func)
            elif feature == "logs":
                _reset_screen()
                message = show_logs_action(ctx, input_func=input_func)
            elif feature == "boot_recovery_status":
                _reset_screen()
                message = boot_recovery_status_action(ctx)
            elif feature == "windows_partition_extend":
                _reset_screen()
                message = windows_partition_extend_action(ctx, input_func=input_func)
            elif feature == "clear_restore_failure_lock":
                _reset_screen()
                message = clear_restore_failure_lock_action(ctx, input_func=input_func)
            elif feature == "reboot_windows":
                _reset_screen()
                message = reboot_to_windows_action(ctx, input_func=input_func)
                if "rebooting" in message.lower():
                    ctx.runtime_state.last_message = message
                    print()
                    print(message)
                    print()
                    print("Rebooting...")
                    time.sleep(5)
                    return 0
            else:
                message = "Unknown menu item."

            if message == LOG_MENU_RETURN:
                ctx.runtime_state.last_message = ""
                continue

            ctx.runtime_state.last_message = message
            if feature == "backup" and message == "Backup canceled.":
                continue
            print()
            print(message)
            print()
            if (
                feature
                in (
                    "status",
                    "restore",
                    "delete_backup",
                    "backup",
                    "boot_recovery_status",
                    "windows_partition_extend",
                    "clear_restore_failure_lock",
                )
                and "Returning to main menu." not in message
                and "Returning to administrator menu." not in message
            ):
                print("Returning to main menu.")
            print("Press Enter to return.")
            read_choice(input_func=input_func, prompt="", accept_enter=True)
        except Exception as exc:
            from recovery_runtime.ui_helpers import append_error_log

            msg = f"Menu error captured; runtime continues: {exc}"
            if ctx.recovery_root:
                append_error_log(ctx.recovery_root, msg)
            ctx.runtime_state.last_message = msg
            print()
            print(msg)

"""Recovery Runtime TUI menu (keyboard navigation, no desktop GUI)."""

from __future__ import annotations

import sys
from typing import Callable

from recovery_runtime.actions import (
    delete_backup_action,
    run_backup_action,
    run_restore_action,
    show_logs_action,
    show_status_action,
)
from recovery_runtime.runtime_context import RuntimeContext
from recovery_runtime.ui_helpers import default_input

InputFunc = Callable[[str], str]

MENU_ITEMS = (
    ("1", "시스템 상태 확인", "status"),
    ("2", "백업 생성", "backup"),
    ("3", "복구 실행", "restore"),
    ("4", "기존 백업 삭제", "delete_backup"),
    ("5", "로그 보기", "logs"),
    ("6", "종료", "exit"),
)


def render_header(ctx: RuntimeContext) -> None:
    print()
    print("=" * 60)
    print(" Recovery Platform — Linux Recovery Runtime")
    print("=" * 60)
    for line in ctx.runtime_state.summary_lines():
        print(f"  {line}")
    print("=" * 60)


def _menu_suffix(ctx: RuntimeContext, feature: str) -> str:
    menu = ctx.menu
    if feature == "backup":
        if not menu.backup_visible:
            return " [hidden]"
        if not menu.backup_executable:
            return f" [disabled: {menu.backup_reason}]"
        return ""
    if feature == "restore":
        if not menu.restore_visible:
            return " [hidden]"
        if not menu.restore_executable:
            return f" [disabled: {menu.restore_reason}]"
        return ""
    if feature == "delete_backup":
        if not menu.delete_visible:
            return " [hidden]"
        if not menu.delete_executable:
            return f" [disabled: {menu.delete_reason}]"
        return ""
    return ""


def render_menu(ctx: RuntimeContext) -> None:
    render_header(ctx)
    print()
    for key, label, feature in MENU_ITEMS:
        suffix = _menu_suffix(ctx, feature)
        print(f"  {key}. {label}{suffix}")
    print()


def read_choice(*, input_func: InputFunc = default_input) -> str:
    try:
        return input_func("선택> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "6"


def run_menu_loop(
    ctx: RuntimeContext,
    *,
    input_func: InputFunc = default_input,
) -> int:
    """Run interactive menu until exit. Exceptions must not crash the runtime."""
    key_to_feature = {key: feature for key, _label, feature in MENU_ITEMS}

    while True:
        try:
            ctx.refresh()
            render_menu(ctx)
            choice = read_choice(input_func=input_func)
            feature = key_to_feature.get(choice)
            if feature is None:
                ctx.runtime_state.last_message = f"잘못된 선택: {choice!r}"
                continue
            if feature == "exit":
                print("종료합니다.")
                return 0

            if feature == "status":
                message = show_status_action(ctx)
            elif feature == "backup":
                message = run_backup_action(ctx, input_func=input_func)
            elif feature == "restore":
                message = run_restore_action(ctx, input_func=input_func)
            elif feature == "delete_backup":
                message = delete_backup_action(ctx, input_func=input_func)
            elif feature == "logs":
                message = show_logs_action(ctx, input_func=input_func)
            else:
                message = "알 수 없는 메뉴 항목입니다."

            ctx.runtime_state.last_message = message
            print()
            print(message)
        except Exception as exc:
            from recovery_runtime.ui_helpers import append_error_log

            msg = f"메뉴 오류 (runtime continues): {exc}"
            if ctx.recovery_root:
                append_error_log(ctx.recovery_root, msg)
            ctx.runtime_state.last_message = msg
            print()
            print(msg)

        if not sys.stdin.isatty():
            return 0

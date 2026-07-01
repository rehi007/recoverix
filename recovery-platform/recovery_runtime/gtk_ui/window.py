"""GTK 3 Recoverix Recovery UI."""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from backup_engine.backup_finalize import FinalizeCheckResult, run_backup_finalize_check
from recovery_runtime.admin_bridge import (
    helper_available,
    run_runtime_admin_json,
    run_runtime_admin_streaming_json,
)
from recovery_runtime.gtk_ui.gtk_compat import gtk_label_set_line_spacing, gtk_version_summary
from recovery_runtime.gtk_ui.image_status import (
    ImageStatus,
    UIButtonState,
    effective_status_for_ui,
)
from recovery_runtime.gtk_ui.logging_util import ui_log
from restore_engine.confirmation import RESTORE_CONFIRMATION_PHRASE
from restore_engine.restore_state import logs_dir

ADMIN_ACCEL = "<Control>a"

PROGRESS_DETAIL_KO = {
    "Working": "작업 중",
    "Preparing backup workspace": "백업 작업 공간 준비 중",
    "Preparing backup workspace...": "백업 작업 공간 준비 중",
    "Backing up Windows partition": "Windows 파티션 백업 중",
    "Verifying Windows backup image": "Windows 백업 이미지 검증 중",
    "Backing up EFI partition": "EFI 파티션 백업 중",
    "Verifying EFI backup image": "EFI 백업 이미지 검증 중",
    "Backing up GPT metadata": "GPT 메타데이터 백업 중",
    "Verifying GPT metadata backup": "GPT 메타데이터 백업 검증 중",
    "Collecting backup metadata": "백업 메타데이터 수집 중",
    "Generating backup hashes": "백업 해시 생성 중",
    "Validating backup hashes": "백업 해시 검증 중",
    "Finalizing backup metadata": "백업 메타데이터 마무리 중",
    "Recovery backup completed.": "복구 백업 완료",
    "Checking restore readiness": "복원 준비 상태 확인 중",
    "Restore readiness verified": "복원 준비 상태 확인 완료",
    "Backing up EFI safety files": "EFI 안전 파일 백업 중",
    "Preparing Windows partition": "Windows 파티션 준비 중",
    "Windows partition restored": "Windows 파티션 복원 완료",
    "Finalizing system restore": "시스템 복원 마무리 중",
    "Restoring RecoveryBoot EFI files": "RecoveryBoot EFI 파일 복원 중",
    "Verifying Windows Boot Manager": "Windows 부팅 관리자 검증 중",
    "Checking RecoveryBoot order": "RecoveryBoot 부팅 순서 확인 중",
    "System restore completed.": "시스템 복원 완료",
    "Restoring Windows partition": "Windows 파티션 복원 중",
}


def _ko_bool(value: bool) -> str:
    return "예" if value else "아니오"


@dataclass
class OperationResult:
    """Result passed from worker threads back to the GTK main loop."""

    ok: bool
    title: str
    body: str
    payload: dict[str, Any] | None = None
    finalize: FinalizeCheckResult | None = None


class RecoveryWindow(Gtk.Window):
    """Main Recoverix recovery application window."""

    def __init__(
        self,
        *,
        marker: Path | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(title="Recoverix 복구 솔루션")
        self._marker = marker
        self._log = log_fn or ui_log
        self._admin_mode = False
        self._busy = False
        self._status: ImageStatus
        self._buttons: UIButtonState
        self._status_values: dict[str, Gtk.Label] = {}
        self._all_action_buttons: list[Gtk.Button] = []
        self._button_descriptions: dict[Gtk.Button, tuple[str, str]] = {}
        self._hover_tooltip_window: Gtk.Window | None = None
        self._hover_tooltip_title: Gtk.Label | None = None
        self._hover_tooltip_body: Gtk.Label | None = None

        self.set_default_size(1080, 700)
        self.set_border_width(0)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_decorated(False)
        self.connect("destroy", Gtk.main_quit)
        self._install_css()

        self._build_layout()
        self._connect_visibility_logs()
        self._install_admin_accelerator()
        self._refresh_state()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            window {
                background: #f4f6f8;
                color: #17202a;
                font-family: "Noto Sans CJK KR", "Noto Sans CJK", "DejaVu Sans", sans-serif;
            }
            .page {
                padding: 24px;
            }
            .title {
                font-size: 24px;
                font-weight: 700;
            }
            .subtitle {
                color: #56616f;
                font-size: 12px;
            }
            .panel {
                background: #ffffff;
                border: 1px solid #d7dde4;
                border-radius: 8px;
                padding: 16px;
            }
            .section-title {
                font-size: 15px;
                font-weight: 700;
            }
            .status-name {
                color: #56616f;
                font-size: 12px;
            }
            .status-value {
                color: #17202a;
                font-size: 13px;
                font-weight: 600;
            }
            .primary-action {
                background-image: none;
                background: #1f6feb;
                color: #ffffff;
                border: 1px solid #1558c0;
                border-radius: 6px;
                min-height: 46px;
                font-weight: 700;
            }
            .primary-action:hover {
                background: #0b4fb3;
                border-color: #073b86;
                box-shadow: inset 0 0 0 2px #073b86;
            }
            .secondary-action {
                background-image: none;
                background: #eef2f7;
                border: 1px solid #b8c3d1;
                color: #17202a;
                border-radius: 6px;
                min-height: 42px;
            }
            .secondary-action:hover {
                background: #cbd5e1;
                border-color: #475569;
                box-shadow: inset 0 0 0 2px #64748b;
            }
            .reboot-action {
                background-image: none;
                background: #0f766e;
                color: #ffffff;
                border: 1px solid #0f5f59;
                border-radius: 6px;
                min-height: 42px;
                font-weight: 700;
            }
            .reboot-action:hover {
                background: #064e3b;
                border-color: #04382b;
                box-shadow: inset 0 0 0 2px #04382b;
            }
            .danger-action {
                background-image: none;
                background: #b42318;
                color: #ffffff;
                border: 1px solid #912018;
                border-radius: 6px;
                min-height: 42px;
                font-weight: 700;
            }
            .danger-action:hover {
                background: #7a1b14;
                border-color: #5f140f;
                box-shadow: inset 0 0 0 2px #5f140f;
            }
            .quiet-action {
                background-image: none;
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                color: #17202a;
                border-radius: 6px;
                min-height: 38px;
            }
            .quiet-action:hover {
                background: #dbe4ee;
                border-color: #64748b;
                box-shadow: inset 0 0 0 2px #94a3b8;
            }
            .primary-action:disabled,
            .secondary-action:disabled,
            .reboot-action:disabled,
            .danger-action:disabled,
            .quiet-action:disabled,
            .primary-action:disabled:hover,
            .secondary-action:disabled:hover,
            .reboot-action:disabled:hover,
            .danger-action:disabled:hover,
            .quiet-action:disabled:hover {
                background-image: none;
                background: #e5e7eb;
                border-color: #cbd5e1;
                color: #64748b;
                text-shadow: none;
                box-shadow: none;
            }
            .message {
                color: #344054;
                font-size: 13px;
            }
            .hint {
                color: #56616f;
                font-size: 12px;
            }
            .dialog-title {
                color: #17202a;
                font-size: 21px;
                font-weight: 700;
            }
            .dialog-body {
                color: #344054;
                font-size: 14px;
            }
            .hover-tooltip {
                background: #17202a;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 10px;
            }
            .hover-tooltip-title {
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
            }
            .hover-tooltip-body {
                color: #e5e7eb;
                font-size: 12px;
            }
            """
        )
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def _build_layout(self) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        page.get_style_context().add_class("page")
        self.add(page)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        page.pack_start(header, False, False, 0)
        header.set_hexpand(True)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.pack_start(title_box, True, True, 0)

        title = Gtk.Label(label="Recoverix 복구 솔루션")
        title.get_style_context().add_class("title")
        title.set_halign(Gtk.Align.START)
        title_box.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(label="시스템 백업, 복원 및 복구 관리")
        subtitle.get_style_context().add_class("subtitle")
        subtitle.set_halign(Gtk.Align.START)
        title_box.pack_start(subtitle, False, False, 0)

        header_status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header_status.set_halign(Gtk.Align.END)
        header_status.set_hexpand(False)
        header.pack_end(header_status, False, False, 0)

        self._mode_label = Gtk.Label()
        self._mode_label.get_style_context().add_class("subtitle")
        self._mode_label.set_halign(Gtk.Align.END)
        header_status.pack_start(self._mode_label, False, False, 0)

        self._exit_btn = self._button("PC 재부팅", "reboot-action")
        self._exit_btn.set_size_request(160, 38)
        self._exit_btn.set_halign(Gtk.Align.END)
        self._exit_btn.connect("clicked", self._on_reboot_clicked)
        self._register_button_description(
            self._exit_btn,
            "PC 재부팅",
            "Recoverix 복구 환경을 종료하고 PC를 재부팅합니다.",
        )
        header_status.pack_start(self._exit_btn, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        page.pack_start(content, True, True, 0)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.pack_start(left, True, True, 0)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._admin_sidebar = right
        self._admin_sidebar.set_no_show_all(True)
        right.set_size_request(320, -1)
        right.set_hexpand(False)
        right.set_halign(Gtk.Align.FILL)
        content.pack_start(right, False, False, 0)

        status_panel = self._panel("시스템 상태")
        self._info_title = status_panel.get_children()[0]
        left.pack_start(status_panel, False, False, 0)
        self._build_info_panel(status_panel)

        action_panel = self._panel("복구 작업")
        left.pack_start(action_panel, False, False, 0)
        self._build_action_buttons(action_panel)

        progress_panel = self._panel("현재 작업")
        left.pack_start(progress_panel, True, True, 0)
        self._build_progress_area(progress_panel)

        self._admin_panel = self._panel("관리자")
        right.pack_start(self._admin_panel, False, False, 0)
        self._build_admin_panel(self._admin_panel)

    def _panel(self, title: str) -> Gtk.Box:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.get_style_context().add_class("panel")
        heading = Gtk.Label(label=title)
        heading.get_style_context().add_class("section-title")
        heading.set_halign(Gtk.Align.START)
        panel.pack_start(heading, False, False, 0)
        return panel

    def _build_info_panel(self, panel: Gtk.Box) -> None:
        self._info_stack = Gtk.Stack()
        self._info_stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._info_stack.set_transition_duration(0)
        panel.pack_start(self._info_stack, True, True, 0)

        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._info_stack.add_named(status_box, "status")
        self._build_status_grid(status_box)

        notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._info_stack.add_named(notes_box, "notes")
        self._build_notes_panel(notes_box)
        self._show_status_panel()

    def _build_status_grid(self, panel: Gtk.Box) -> None:
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(20)
        panel.pack_start(grid, False, False, 0)

        rows = [
            ("mode", "모드"),
            ("recovery_image", "복구 이미지 파티션"),
            ("backup_image", "백업 이미지"),
            ("backup_type", "백업 유형"),
            ("restore", "복원"),
            ("device", "장치"),
            ("mount", "마운트"),
            ("warning", "알림"),
        ]
        for row, (key, name) in enumerate(rows):
            name_label = Gtk.Label(label=name)
            name_label.get_style_context().add_class("status-name")
            name_label.set_halign(Gtk.Align.START)
            value_label = Gtk.Label(label="-")
            value_label.get_style_context().add_class("status-value")
            value_label.set_halign(Gtk.Align.START)
            value_label.set_selectable(True)
            value_label.set_line_wrap(True)
            self._status_values[key] = value_label
            grid.attach(name_label, 0, row, 1, 1)
            grid.attach(value_label, 1, row, 1, 1)

    def _build_action_buttons(self, panel: Gtk.Box) -> None:
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(10)
        panel.pack_start(grid, False, False, 0)

        self._status_btn = self._button("상태 새로고침", "secondary-action")
        self._backup_btn = self._button("복구 백업 생성", "primary-action")
        self._restore_btn = self._button("시스템 복원", "danger-action")
        self._reboot_btn = self._button("Windows로 재부팅", "reboot-action")

        self._status_btn.connect("clicked", self._on_status_clicked)
        self._backup_btn.connect("clicked", self._on_backup_clicked)
        self._restore_btn.connect("clicked", self._on_restore_clicked)
        self._reboot_btn.connect("clicked", self._on_reboot_clicked)
        self._register_button_description(
            self._status_btn,
            "상태 새로고침",
            "현재 복구 이미지, 백업 이미지, 복원 가능 여부를 다시 확인합니다.",
        )
        self._register_button_description(
            self._backup_btn,
            "복구 백업 생성",
            "현재 Windows 시스템 상태를 RECOVERY_IMAGE에 백업합니다. 정상 백업이 이미 있으면 새 백업을 만들 수 없습니다.",
        )
        self._register_button_description(
            self._restore_btn,
            "시스템 복원",
            "저장된 백업 이미지로 Windows 시스템 파티션을 복원합니다. 현재 Windows 파티션의 데이터는 삭제됩니다.",
        )
        self._register_button_description(
            self._reboot_btn,
            "Windows로 재부팅",
            "Recoverix 복구 환경을 종료하고 Windows로 재부팅합니다.",
        )

        grid.attach(self._status_btn, 0, 0, 1, 1)
        grid.attach(self._backup_btn, 1, 0, 1, 1)
        grid.attach(self._restore_btn, 0, 1, 1, 1)
        grid.attach(self._reboot_btn, 1, 1, 1, 1)

        self._backup_hint = Gtk.Label(label="")
        self._backup_hint.get_style_context().add_class("hint")
        self._backup_hint.set_halign(Gtk.Align.START)
        self._backup_hint.set_xalign(0.0)
        self._backup_hint.set_line_wrap(True)
        gtk_label_set_line_spacing(self._backup_hint, 4)
        panel.pack_start(self._backup_hint, False, False, 0)

    def _build_progress_area(self, panel: Gtk.Box) -> None:
        self._operation_label = Gtk.Label(label="대기 중.")
        self._operation_label.get_style_context().add_class("message")
        self._operation_label.set_halign(Gtk.Align.START)
        self._operation_label.set_line_wrap(True)
        gtk_label_set_line_spacing(self._operation_label, 5)
        panel.pack_start(self._operation_label, False, False, 0)

        self._progress = Gtk.ProgressBar()
        self._progress.set_show_text(True)
        self._progress.set_text("")
        panel.pack_start(self._progress, False, False, 0)

    def _build_admin_panel(self, panel: Gtk.Box) -> None:
        self._admin_locked_label = Gtk.Label(
            label="관리자 메뉴가 잠겨 있습니다.\nCtrl+A를 누르면 관리자 도구가 표시됩니다."
        )
        self._admin_locked_label.get_style_context().add_class("hint")
        self._admin_locked_label.set_halign(Gtk.Align.START)
        self._admin_locked_label.set_xalign(0.0)
        self._admin_locked_label.set_line_wrap(True)
        self._admin_locked_label.set_no_show_all(True)
        gtk_label_set_line_spacing(self._admin_locked_label, 4)
        panel.pack_start(self._admin_locked_label, False, False, 0)

        self._admin_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._admin_box.set_no_show_all(True)
        panel.pack_start(self._admin_box, False, False, 0)

        self._delete_btn = self._button("백업 삭제", "danger-action")
        self._logs_btn = self._button("진단 로그", "secondary-action")
        self._extend_btn = self._button("복원 준비", "secondary-action")
        self._clear_lock_btn = self._button("복원 잠금 해제", "secondary-action")
        self._boot_status_btn = self._button("부팅 상태", "secondary-action")
        self._standard_btn = self._button("일반 모드", "quiet-action")

        self._delete_btn.connect("clicked", self._on_delete_clicked)
        self._logs_btn.connect("clicked", self._on_logs_clicked)
        self._extend_btn.connect("clicked", self._on_windows_extend_clicked)
        self._clear_lock_btn.connect("clicked", self._on_clear_lock_clicked)
        self._boot_status_btn.connect("clicked", self._on_boot_status_clicked)
        self._standard_btn.connect("clicked", self._return_standard_mode)
        self._register_button_description(
            self._delete_btn,
            "백업 삭제",
            "RECOVERY_IMAGE에 저장된 기존 백업 이미지를 삭제합니다. 새 백업을 만들기 전에 사용할 수 있습니다.",
        )
        self._register_button_description(
            self._logs_btn,
            "진단 로그",
            "복구 런타임, 백업, 복원 관련 로그를 정보 패널에 표시합니다.",
        )
        self._register_button_description(
            self._extend_btn,
            "복원 준비",
            "Windows 파티션이 백업 이미지 복원에 충분한지 확인하고, 바로 뒤의 빈 공간만 사용해 파티션 경계를 준비합니다.",
        )
        self._register_button_description(
            self._clear_lock_btn,
            "복원 잠금 해제",
            "실패한 복원 작업 때문에 잠긴 상태를 해제합니다. 백업 이미지는 삭제하지 않습니다.",
        )
        self._register_button_description(
            self._boot_status_btn,
            "부팅 상태",
            "Recoverix 부팅 항목, Windows 부팅 항목, 현재 복구 상태를 정보 패널에 표시합니다.",
        )
        self._register_button_description(
            self._standard_btn,
            "일반 모드",
            "관리자 메뉴를 숨기고 일반 사용자 화면으로 돌아갑니다. Ctrl+A로도 전환할 수 있습니다.",
        )

        self._admin_buttons = [
            self._delete_btn,
            self._logs_btn,
            self._extend_btn,
            self._clear_lock_btn,
            self._boot_status_btn,
            self._standard_btn,
        ]
        for button in (
            *self._admin_buttons,
        ):
            self._admin_box.pack_start(button, False, False, 0)

    def _build_notes_panel(self, panel: Gtk.Box) -> None:
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(230)
        panel.pack_start(scrolled, True, True, 0)

        self._notes_view = Gtk.TextView()
        self._notes_view.set_editable(False)
        self._notes_view.set_cursor_visible(False)
        self._notes_view.set_monospace(True)
        self._notes_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._notes_view.set_left_margin(8)
        self._notes_view.set_right_margin(8)
        self._notes_view.set_top_margin(8)
        self._notes_view.set_bottom_margin(8)
        self._notes_view.get_buffer().set_text("작업을 선택하면 상세 내용과 결과가 여기에 표시됩니다.")
        scrolled.add(self._notes_view)

    def _button(self, label: str, css_class: str) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.get_style_context().add_class(css_class)
        button.set_hexpand(True)
        button.set_halign(Gtk.Align.FILL)
        self._all_action_buttons.append(button)
        return button

    def _register_button_description(self, button: Gtk.Button, title: str, body: str) -> None:
        self._button_descriptions[button] = (title, body)
        button.set_has_tooltip(False)
        button.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        button.connect("enter-notify-event", self._on_button_hover_enter)
        button.connect("leave-notify-event", self._on_button_hover_leave)

    def _on_button_hover_enter(self, button: Gtk.Button, _event: object) -> bool:
        description = self._button_descriptions.get(button)
        if description is None:
            return False
        title, body = description
        self._set_notes_text("작업 설명", f"{title}\n\n{body}")
        self._show_hover_tooltip(button, title, body)
        return False

    def _on_button_hover_leave(self, _button: Gtk.Button, _event: object) -> bool:
        self._hide_hover_tooltip()
        self._show_status_panel()
        return False

    def _ensure_hover_tooltip(self) -> tuple[Gtk.Window, Gtk.Label, Gtk.Label]:
        if (
            self._hover_tooltip_window is not None
            and self._hover_tooltip_title is not None
            and self._hover_tooltip_body is not None
        ):
            return self._hover_tooltip_window, self._hover_tooltip_title, self._hover_tooltip_body

        popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        popup.set_decorated(False)
        popup.set_resizable(False)
        popup.set_accept_focus(False)
        popup.set_focus_on_map(False)
        popup.set_skip_taskbar_hint(True)
        popup.set_skip_pager_hint(True)
        popup.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        popup.set_transient_for(self)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.get_style_context().add_class("hover-tooltip")
        outer.set_size_request(340, -1)
        popup.add(outer)

        title_label = Gtk.Label()
        title_label.get_style_context().add_class("hover-tooltip-title")
        title_label.set_halign(Gtk.Align.START)
        title_label.set_xalign(0.0)
        title_label.set_line_wrap(True)
        title_label.set_max_width_chars(34)
        outer.pack_start(title_label, False, False, 0)

        body_label = Gtk.Label()
        body_label.get_style_context().add_class("hover-tooltip-body")
        body_label.set_halign(Gtk.Align.START)
        body_label.set_xalign(0.0)
        body_label.set_line_wrap(True)
        body_label.set_max_width_chars(42)
        gtk_label_set_line_spacing(body_label, 3)
        outer.pack_start(body_label, False, False, 0)

        self._hover_tooltip_window = popup
        self._hover_tooltip_title = title_label
        self._hover_tooltip_body = body_label
        return popup, title_label, body_label

    def _show_hover_tooltip(self, button: Gtk.Button, title: str, body: str) -> None:
        popup, title_label, body_label = self._ensure_hover_tooltip()
        title_label.set_text(title)
        body_label.set_text(body)

        root_window = self.get_window()
        if root_window is None:
            return
        translated = button.translate_coordinates(self, 0, 0)
        if translated is None:
            return

        root_origin = root_window.get_origin()
        if len(root_origin) == 3:
            _ok, root_x, root_y = root_origin
        else:
            root_x, root_y = root_origin

        allocation = button.get_allocation()
        x = int(root_x + translated[0] + allocation.width + 12)
        y = int(root_y + translated[1])

        display = Gdk.Display.get_default()
        if display is not None:
            monitor = display.get_monitor_at_point(x, y)
            if monitor is not None:
                geometry = monitor.get_geometry()
                _minimum, natural = popup.get_preferred_size()
                width = max(natural.width, 340)
                height = max(natural.height, 80)
                if x + width > geometry.x + geometry.width - 12:
                    x = int(root_x + translated[0] - width - 12)
                y = min(y, geometry.y + geometry.height - height - 12)
                x = max(x, geometry.x + 12)
                y = max(y, geometry.y + 12)

        popup.move(x, y)
        popup.show_all()

    def _hide_hover_tooltip(self) -> None:
        if self._hover_tooltip_window is not None:
            self._hover_tooltip_window.hide()

    def _connect_visibility_logs(self) -> None:
        self.connect("realize", self._on_realize)
        self.connect("map-event", self._on_map_event)
        self.connect("show", self._on_show)
        self.connect("delete-event", self._on_delete_event)

    def _install_admin_accelerator(self) -> None:
        accel_group = Gtk.AccelGroup()
        self.add_accel_group(accel_group)
        key, mod = Gtk.accelerator_parse(ADMIN_ACCEL)
        accel_group.connect(
            key,
            mod,
            Gtk.AccelFlags.VISIBLE,
            self._toggle_admin_mode,
        )

    def _on_realize(self, _widget: Gtk.Widget) -> None:
        self._log("window signal: realize")

    def _on_map_event(self, _widget: Gtk.Widget, _event: object) -> bool:
        self._log("window signal: map-event")
        return False

    def _on_show(self, _widget: Gtk.Widget) -> None:
        self._log("window signal: show")

    def _on_delete_event(self, _widget: Gtk.Widget, _event: object) -> bool:
        self._log("window signal: delete-event")
        self._request_reboot_to_windows(
            title="Recoverix 복구 종료",
            body=(
                "복구 화면은 데스크톱으로 종료할 수 없습니다.\n\n"
                "이 컴퓨터를 Windows로 재부팅하시겠습니까?"
            ),
        )
        return True

    def _activate_admin_mode(self, *_args: object) -> bool:
        if self._admin_mode:
            return True
        self._admin_mode = True
        self._log("admin mode activation")
        self._set_operation_message("관리자 모드가 활성화되었습니다.")
        self._refresh_state()
        return True

    def _toggle_admin_mode(self, *_args: object) -> bool:
        if self._admin_mode:
            self._return_standard_mode()
            return True
        return self._activate_admin_mode()

    def _return_standard_mode(self, *_args: object) -> None:
        self._admin_mode = False
        self._log("administrator mode disabled")
        self._set_operation_message("일반 모드가 활성화되었습니다.")
        self._refresh_state()
        self._show_status_panel()

    def _refresh_state(self) -> None:
        self._status, self._buttons = effective_status_for_ui(admin_mode=self._admin_mode)
        self._update_status_labels()
        self._update_button_state()
        self._log(
            "ui_state "
            f"mode={'admin' if self._admin_mode else 'standard'} "
            f"partition_found={self._status.recovery_image_partition_found} "
            f"mounted={self._status.mounted} "
            f"valid_backup={self._status.valid_backup_exists}"
        )

    def _update_status_labels(self) -> None:
        mode = "관리자" if self._admin_mode else "일반"
        backup_type = self._detect_backup_type(self._status)
        restore_state = "사용 가능" if self._buttons.restore_sensitive else "비활성화"
        self._mode_label.set_text(f"{mode} 모드")
        self._status_values["mode"].set_text(mode)
        self._status_values["recovery_image"].set_text(
            "감지됨" if self._status.recovery_image_partition_found else "없음"
        )
        self._status_values["backup_image"].set_text(
            "정상 백업" if self._status.valid_backup_exists else "생성되지 않음"
        )
        self._status_values["backup_type"].set_text(backup_type)
        self._status_values["restore"].set_text(restore_state)
        self._status_values["device"].set_text(self._status.device or "-")
        self._status_values["mount"].set_text(
            str(self._status.mount_point) if self._status.mounted and self._status.mount_point else "마운트되지 않음"
        )
        self._status_values["warning"].set_text(self._buttons.warning or "-")
        self._backup_hint.set_text(self._backup_action_hint())

    def _update_button_state(self) -> None:
        for button in self._all_action_buttons:
            button.set_sensitive(not self._busy)

        if self._admin_mode:
            self._admin_sidebar.set_no_show_all(False)
            self._admin_box.set_no_show_all(False)
            self._admin_sidebar.show()
            self._admin_panel.show_all()
            self._admin_box.show()
            self._admin_locked_label.hide()
            for button in self._admin_buttons:
                button.show_all()
        else:
            for button in self._admin_buttons:
                button.hide()
            self._admin_locked_label.hide()
            self._admin_box.hide()
            self._admin_panel.hide()
            self._admin_sidebar.hide()
            self._admin_sidebar.set_no_show_all(True)
            self._admin_box.set_no_show_all(True)

        if self._busy:
            return

        self._backup_btn.set_sensitive(self._buttons.backup_sensitive)
        self._restore_btn.set_sensitive(self._buttons.restore_sensitive)
        self._delete_btn.set_sensitive(self._admin_mode and self._buttons.delete_sensitive)
        self._delete_btn.set_visible(self._admin_mode)

    def _backup_action_hint(self) -> str:
        if not self._status.recovery_image_partition_found:
            return "백업 불가: RECOVERY_IMAGE 파티션을 찾을 수 없습니다."
        if not self._status.mounted:
            return "백업 불가: RECOVERY_IMAGE가 마운트되지 않았습니다."
        if self._status.valid_backup_exists:
            backup_type = self._detect_backup_type(self._status)
            if backup_type == "-":
                backup_type = "정상 백업"
            return (
                f"백업이 이미 존재합니다({backup_type}). "
                "새 백업을 만들려면 관리자 모드에서 기존 백업을 삭제하세요."
            )
        return "정상 백업 이미지가 없습니다. 복구 백업 생성이 가능합니다."

    @staticmethod
    def _detect_backup_type(status: ImageStatus) -> str:
        if not status.valid_backup_exists or status.mount_point is None:
            return "-"
        manifest_paths = [
            status.mount_point / "manifests" / "recovery-manifest.json",
            status.mount_point / "metadata" / "recovery-manifest.json",
        ]
        for path in manifest_paths:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if '"backup_type": "admin_compact"' in text:
                return "관리자 백업"
            if '"backup_type": "standard"' in text:
                return "일반 백업"
        return "정상 백업"

    @staticmethod
    def _format_bytes(num: int | None) -> str:
        if num is None:
            return "알 수 없음"
        value = float(num)
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        idx = 0
        while value >= 1024.0 and idx < len(units) - 1:
            value /= 1024.0
            idx += 1
        if idx == 0:
            return f"{int(value)} {units[idx]}"
        return f"{value:.2f} {units[idx]}"

    def _precheck_block_reason(self, status: ImageStatus) -> str | None:
        if not status.recovery_image_partition_found:
            return "RECOVERY_IMAGE 파티션을 찾을 수 없습니다."
        if not status.mounted:
            return "RECOVERY_IMAGE가 마운트되지 않았습니다."
        return None

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self._busy = busy
        if message is not None:
            self._set_operation_message(message)
        self._update_button_state()

    def _set_operation_message(self, message: str, *, percent: int | None = None) -> None:
        self._operation_label.set_text(message)
        if percent is None:
            self._progress.set_fraction(0.0)
            self._progress.set_text("")
            return
        clamped = max(0, min(100, int(percent)))
        self._progress.set_fraction(clamped / 100.0)
        self._progress.set_text(f"{clamped}%")

    def _apply_progress_event(self, event: dict[str, Any]) -> None:
        percent, message = self._format_progress_event(event)
        self._set_operation_message(message, percent=percent)

    @staticmethod
    def _format_progress_event(event: dict[str, Any]) -> tuple[int, str]:
        percent = int(event.get("percent") or 0)
        detail = str(event.get("detail") or event.get("message") or "Working")
        final = bool(event.get("final"))
        prefix = "완료" if final or percent >= 100 else "진행 중"
        translated_detail = PROGRESS_DETAIL_KO.get(detail, detail)
        return max(0, min(100, percent)), f"{prefix}: {translated_detail}"

    def _set_notes_text(self, title: str, body: str) -> None:
        self._info_title.set_text(title)
        self._info_stack.set_visible_child_name("notes")
        buffer = self._notes_view.get_buffer()
        buffer.set_text(body)

    def _show_status_panel(self) -> None:
        self._info_title.set_text("시스템 상태")
        self._info_stack.set_visible_child_name("status")

    def _bind_dialog_close(
        self,
        dialog: Gtk.Dialog,
        response: Gtk.ResponseType,
        response_holder: dict[str, Any],
    ) -> None:
        def on_delete(_dialog: Gtk.Dialog, _event: object) -> bool:
            response_holder["response"] = response
            dialog.response(response)
            return True

        dialog.connect("delete-event", on_delete)

    def _configure_dialog(self, dialog: Gtk.Dialog) -> None:
        dialog.set_modal(True)
        dialog.set_deletable(False)
        dialog.set_decorated(False)
        dialog.set_resizable(False)
        dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)
        dialog.set_keep_above(True)

    def _show_message(
        self,
        title: str,
        body: str,
        *,
        message_type: Gtk.MessageType = Gtk.MessageType.INFO,
    ) -> None:
        self._set_notes_text(title, body)
        self._set_operation_message(title)
        dialog = self._build_recoverix_dialog(title, body)
        ok_button = dialog.add_button("확인", Gtk.ResponseType.OK)
        ok_button.get_style_context().add_class("primary-action")
        response_holder: dict[str, Any] = {}
        self._bind_dialog_close(dialog, Gtk.ResponseType.OK, response_holder)
        dialog.show_all()
        response = dialog.run()
        if response_holder.get("response") is None:
            response_holder["response"] = response
        dialog.destroy()

    def _confirm(
        self,
        title: str,
        body: str,
        *,
        accept_label: str = "계속",
        message_type: Gtk.MessageType = Gtk.MessageType.QUESTION,
    ) -> bool:
        self._set_notes_text(title, body)
        dialog = self._build_recoverix_dialog(title, body)
        cancel_button = dialog.add_button("취소", Gtk.ResponseType.CANCEL)
        accept_button = dialog.add_button(accept_label, Gtk.ResponseType.OK)
        cancel_button.get_style_context().add_class("quiet-action")
        accept_button.get_style_context().add_class(
            "danger-action" if message_type == Gtk.MessageType.WARNING else "primary-action"
        )
        response_holder: dict[str, Any] = {}
        self._bind_dialog_close(dialog, Gtk.ResponseType.CANCEL, response_holder)
        dialog.show_all()
        response = dialog.run()
        if response_holder.get("response") is None:
            response_holder["response"] = response
        dialog.destroy()
        return response_holder["response"] == Gtk.ResponseType.OK

    def _build_recoverix_dialog(self, title: str, body: str) -> Gtk.Dialog:
        dialog = Gtk.Dialog(
            title=title,
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        self._configure_dialog(dialog)
        dialog.set_default_size(560, -1)

        content = dialog.get_content_area()
        content.set_spacing(0)
        content.set_border_width(0)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_border_width(24)
        outer.set_size_request(520, -1)
        content.pack_start(outer, True, True, 0)

        title_label = Gtk.Label(label=title)
        title_label.get_style_context().add_class("dialog-title")
        title_label.set_halign(Gtk.Align.START)
        title_label.set_xalign(0.0)
        title_label.set_line_wrap(True)
        title_label.set_max_width_chars(42)
        outer.pack_start(title_label, False, False, 0)

        body_label = Gtk.Label(label=body)
        body_label.get_style_context().add_class("dialog-body")
        body_label.set_halign(Gtk.Align.START)
        body_label.set_xalign(0.0)
        body_label.set_line_wrap(True)
        body_label.set_max_width_chars(58)
        gtk_label_set_line_spacing(body_label, 6)
        outer.pack_start(body_label, False, False, 0)

        action_area = dialog.get_action_area()
        action_area.set_spacing(10)
        action_area.set_border_width(18)
        return dialog

    def _require_helper(self) -> bool:
        if helper_available():
            return True
        self._show_message(
            "관리자 도우미를 사용할 수 없음",
            "현재 복구 런타임에 Recoverix 관리자 도우미가 설치되어 있지 않습니다.",
            message_type=Gtk.MessageType.ERROR,
        )
        return False

    def _run_worker(
        self,
        *,
        start_message: str,
        worker: Callable[[], OperationResult],
        on_done: Callable[[OperationResult], None] | None = None,
    ) -> None:
        self._set_busy(True, start_message)

        def thread_main() -> None:
            try:
                result = worker()
            except Exception as exc:  # noqa: BLE001
                result = OperationResult(
                    ok=False,
                    title="작업 실패",
                    body=str(exc),
                )

            def complete() -> bool:
                self._set_busy(False)
                if on_done is not None:
                    on_done(result)
                else:
                    self._finish_operation(result)
                return False

            GLib.idle_add(complete)

        threading.Thread(target=thread_main, daemon=True).start()

    def _finish_operation(self, result: OperationResult) -> None:
        self._refresh_state()
        if result.ok:
            self._set_operation_message(result.body)
            self._show_message(result.title, result.body)
        else:
            self._set_operation_message(result.body)
            self._show_message(result.title, result.body, message_type=Gtk.MessageType.ERROR)

    def _on_status_clicked(self, _button: Gtk.Button) -> None:
        self._refresh_state()
        self._set_operation_message("시스템 상태를 새로고침했습니다.")
        self._show_status_panel()

    def _build_status_summary(self) -> str:
        errors = "\n".join(f"- {item}" for item in (self._status.errors or [])) or "- 없음"
        return (
            "Recoverix 시스템 상태\n\n"
            f"모드: {'관리자' if self._admin_mode else '일반'}\n"
            f"복구 이미지 파티션: {'감지됨' if self._status.recovery_image_partition_found else '없음'}\n"
            f"장치: {self._status.device or '-'}\n"
            f"파일시스템: {self._status.filesystem or '-'}\n"
            f"마운트: {_ko_bool(self._status.mounted)}\n"
            f"마운트 위치: {self._status.mount_point or '-'}\n"
            f"매니페스트: {'있음' if self._status.manifest_exists else '없음'}\n"
            f"Windows 이미지: {'있음' if self._status.system_image_exists else '없음'}\n"
            f"EFI 이미지: {'있음' if self._status.esp_image_exists else '없음'}\n"
            f"해시 파일: {'있음' if self._status.hashes_exist else '없음'}\n"
            f"정상 백업: {_ko_bool(self._status.valid_backup_exists)}\n"
            f"백업 유형: {self._detect_backup_type(self._status)}\n\n"
            "작업 상태\n"
            f"백업 작업: {'사용 가능' if self._buttons.backup_sensitive else '비활성화'}\n"
            f"복원 작업: {'사용 가능' if self._buttons.restore_sensitive else '비활성화'}\n"
            f"백업 삭제: {'사용 가능' if self._buttons.delete_sensitive else '비활성화'}\n\n"
            "오류\n"
            f"{errors}"
        )

    @staticmethod
    def _build_firmware_boot_summary(*, returncode: int, stdout: str, stderr: str) -> str:
        if returncode != 0:
            reason = stderr.strip() or f"efibootmgr 종료 코드 {returncode}"
            return (
                "펌웨어 부팅 항목\n\n"
                "상태: 조회 실패\n"
                f"사유: {reason}"
            )

        boot_order_raw = ""
        boot_current_raw = ""
        entries: dict[str, str] = {}
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("BootCurrent:"):
                boot_current_raw = stripped.split(":", 1)[1].strip()
                continue
            if stripped.startswith("BootOrder:"):
                boot_order_raw = stripped.split(":", 1)[1].strip()
                continue
            if not stripped.startswith("Boot") or len(stripped) < 8:
                continue
            number = stripped[4:8]
            if not all(ch in "0123456789ABCDEFabcdef" for ch in number):
                continue
            label = stripped[8:].lstrip("* ").split("\t", 1)[0].strip()
            if label:
                entries[number.upper()] = label

        def entry_label(number: str) -> str:
            normalized = number.strip().upper()
            label = entries.get(normalized, f"Boot{normalized}")
            return f"{label} ({normalized})"

        order_numbers = [item.strip() for item in boot_order_raw.split(",") if item.strip()]
        boot_order = " > ".join(entry_label(item) for item in order_numbers) if order_numbers else "-"
        boot_current = entry_label(boot_current_raw) if boot_current_raw else "-"
        recoverix_exists = any("Recoverix" in label or "RecoveryBoot" in label for label in entries.values())
        windows_exists = any("Windows Boot Manager" in label for label in entries.values())
        existing_os_exists = any(label.lower() == "ubuntu" for label in entries.values())

        return (
            "펌웨어 부팅 항목\n\n"
            f"현재 부팅 항목: {boot_current}\n"
            f"부팅 순서: {boot_order}\n\n"
            f"Recoverix Boot Manager: {'등록됨' if recoverix_exists else '없음'}\n"
            f"Windows Boot Manager: {'등록됨' if windows_exists else '없음'}\n"
            f"Existing OS Boot Manager: {'등록됨' if existing_os_exists else '없음'}"
        )

    def _on_backup_clicked(self, _button: Gtk.Button) -> None:
        self._refresh_state()
        blocked = self._precheck_block_reason(self._status)
        if blocked:
            self._show_message("백업 불가", blocked, message_type=Gtk.MessageType.WARNING)
            return
        if self._status.valid_backup_exists:
            self._show_message(
                "백업이 이미 존재합니다",
                "정상 복구 백업이 이미 존재합니다. 새 백업을 만들려면 기존 백업을 먼저 삭제하세요.",
            )
            return
        if not self._require_helper():
            return

        if not self._confirm(
            title="복구 백업 생성",
            body=(
                "정상 복구 이미지가 없습니다.\n"
                "새 백업을 만들기 전에 RECOVERY_IMAGE를 초기화해야 합니다.\n\n"
                "계속하시겠습니까?"
            ),
            accept_label="계속",
        ):
            self._set_operation_message("백업이 취소되었습니다. 메인 화면으로 돌아갑니다.")
            return
        if not self._confirm(
            title="RECOVERY_IMAGE 초기화",
            body=(
                "경고: RECOVERY_IMAGE에 저장된 모든 데이터가 영구적으로 삭제됩니다.\n"
                "이 작업은 되돌릴 수 없습니다.\n\n"
                "RECOVERY_IMAGE를 지금 초기화하시겠습니까?"
            ),
            accept_label="초기화",
            message_type=Gtk.MessageType.WARNING,
        ):
            self._set_operation_message("초기화가 취소되었습니다. 메인 화면으로 돌아갑니다.")
            return
        self._run_worker(
            start_message="RECOVERY_IMAGE 초기화 중...",
            worker=self._backup_worker,
            on_done=self._finish_backup_operation,
        )

    def _backup_worker(self) -> OperationResult:
        rc, payload, stderr = run_runtime_admin_json("reset-recovery-image")
        if rc != 0 or payload.get("status") != "COMPLETED":
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return OperationResult(False, "백업 실패", f"RECOVERY_IMAGE 초기화 실패: {reason}")

        def on_progress(event: dict[str, Any]) -> None:
            GLib.idle_add(lambda: self._progress_idle(event))

        rc, payload, stderr = run_runtime_admin_streaming_json("run-backup", on_progress=on_progress)
        status = payload.get("status")
        if rc != 0 or status != "COMPLETED":
            reason = str(payload.get("reason") or stderr or status or rc)
            return OperationResult(False, "백업 실패", f"복구 백업 실패: {reason}", payload=payload)

        finalize = self._run_post_backup_finalize_check()
        buttons = effective_status_for_ui(admin_mode=self._admin_mode)[1]
        summary = self._build_backup_success_summary(
            apply_result=SimpleNamespace(status=status),
            finalize=finalize,
            buttons=buttons,
        )
        if not finalize.finalize_ok:
            return OperationResult(
                False,
                "백업 검증 실패",
                summary,
                payload=payload,
                finalize=finalize,
            )
        return OperationResult(True, "백업 완료", summary, payload=payload, finalize=finalize)

    def _progress_idle(self, event: dict[str, Any]) -> bool:
        self._apply_progress_event(event)
        return False

    def _finish_backup_operation(self, result: OperationResult) -> None:
        self._refresh_state()
        if result.ok:
            self._set_operation_message("복구 백업이 완료되었습니다.", percent=100)
            self._show_message(result.title, result.body)
            return
        self._set_operation_message(result.body)
        self._show_message(result.title, result.body, message_type=Gtk.MessageType.ERROR)

    def _run_post_backup_finalize_check(self) -> FinalizeCheckResult:
        """Run recoverix-backup-finalize-check against mounted RECOVERY_IMAGE."""
        self._status, _ = effective_status_for_ui(admin_mode=self._admin_mode)
        if self._status.mount_point is None:
            return FinalizeCheckResult(
                finalize_ok=False,
                valid_backup_exists=False,
                manifest_exists=False,
                hashes_valid=False,
                incomplete_backup_present=True,
                errors=["RECOVERY_IMAGE 마운트 위치를 사용할 수 없습니다."],
            )
        result = run_backup_finalize_check(self._status.mount_point)
        self._log(
            "backup finalize check "
            f"finalize_ok={result.finalize_ok} valid_backup={result.valid_backup_exists} "
            f"errors={len(result.errors)}"
        )
        return result

    def _build_backup_success_summary(
        self,
        *,
        apply_result: Any,
        finalize: FinalizeCheckResult,
        buttons: UIButtonState,
    ) -> str:
        lines = [
            "복구 백업이 완료되었습니다.",
            "",
            f"백업 상태             : {apply_result.status}",
            f"마무리 검증           : {'통과' if finalize.finalize_ok else '실패'}",
            f"매니페스트            : {'생성됨' if finalize.manifest_exists else '없음'}",
            f"해시 검증             : {'통과' if finalize.hashes_valid else '실패'}",
            f"미완료 표시           : {'있음' if finalize.incomplete_backup_present else '정리됨'}",
            f"정상 백업             : {_ko_bool(finalize.valid_backup_exists)}",
            "",
            "화면 상태",
            f"백업 버튼             : {'비활성화' if not buttons.backup_sensitive else '활성화'}",
            f"복원 버튼             : {'활성화' if buttons.restore_sensitive else '비활성화'}",
        ]
        if finalize.errors:
            lines.append("")
            lines.append("마무리 검증 오류")
            for err in finalize.errors:
                lines.append(f"  - {err}")
        return "\n".join(lines)

    def _on_restore_clicked(self, _button: Gtk.Button) -> None:
        self._refresh_state()
        blocked = self._precheck_block_reason(self._status)
        if blocked:
            self._show_message("복원 불가", blocked, message_type=Gtk.MessageType.WARNING)
            return
        if not self._status.valid_backup_exists:
            self._show_message(
                "복원 불가",
                "정상 백업 이미지가 없습니다.",
                message_type=Gtk.MessageType.WARNING,
            )
            return
        if not self._require_helper():
            return

        if not self._confirm(
            title="시스템 복원",
            body=(
                "저장된 복구 이미지를 사용하여 Windows 시스템 파티션을 덮어씁니다.\n\n"
                "계속하시겠습니까?"
            ),
            accept_label="계속",
        ):
            self._set_operation_message("복원이 취소되었습니다. 메인 화면으로 돌아갑니다.")
            return
        if not self._confirm(
            title="시스템 복원 시작",
            body=(
                "경고: Windows 시스템 파티션의 모든 데이터가 영구적으로 삭제됩니다.\n"
                "계속하기 전에 중요한 파일을 다른 위치에 보관하세요.\n\n"
                "지금 시스템 복원을 시작하시겠습니까?"
            ),
            accept_label="복원",
            message_type=Gtk.MessageType.WARNING,
        ):
            self._set_operation_message("복원이 취소되었습니다. 메인 화면으로 돌아갑니다.")
            return
        self._run_worker(
            start_message="복원 준비 상태 확인 중...",
            worker=self._restore_worker,
            on_done=self._finish_restore_operation,
        )

    def _restore_worker(self) -> OperationResult:
        rc, check_payload, stderr = run_runtime_admin_json("check-restore")
        if rc != 0 or check_payload.get("status") != "COMPLETED":
            reason = str(check_payload.get("reason") or stderr or check_payload.get("status") or rc)
            return OperationResult(False, "복원 차단", f"복원을 계속할 수 없습니다: {reason}")

        validation = check_payload.get("validation") or {}
        if validation.get("allowed") is False:
            reason = str(validation.get("reason") or validation.get("status") or "검증 실패")
            return OperationResult(False, "복원 차단", f"복원을 계속할 수 없습니다: {reason}")

        restore_plan = check_payload.get("restore_plan") or {}
        compatible_restore = bool(restore_plan.get("compatible_restore"))
        args = ["run-restore", "--phrase", RESTORE_CONFIRMATION_PHRASE]
        if compatible_restore:
            args.append("--compatible-restore")

        def on_progress(event: dict[str, Any]) -> None:
            GLib.idle_add(lambda: self._progress_idle(event))

        rc, payload, stderr = run_runtime_admin_streaming_json(*args, on_progress=on_progress)
        status = payload.get("status")
        result = payload.get("result") or {}
        if status in ("COMPLETED", "COMPLETED_NEEDS_WINDOWS_CHECK") and result.get("success", True):
            body = "시스템 복원이 완료되었습니다."
            if status == "COMPLETED_NEEDS_WINDOWS_CHECK":
                body = (
                    "시스템 복원이 완료되었습니다.\n\n"
                    "다음 Windows 부팅 시 파일시스템 검사가 필요합니다."
                )
            return OperationResult(True, "복원 완료", body, payload=payload)

        reason = str(payload.get("reason") or result.get("reason") or stderr or status or rc)
        return OperationResult(False, "복원 실패", f"시스템 복원 실패: {reason}", payload=payload)

    def _finish_restore_operation(self, result: OperationResult) -> None:
        self._refresh_state()
        if result.ok:
            self._set_operation_message(result.body, percent=100)
            self._show_message(result.title, result.body)
            return
        self._set_operation_message(result.body)
        self._show_message(result.title, result.body, message_type=Gtk.MessageType.ERROR)

    def _on_delete_clicked(self, _button: Gtk.Button) -> None:
        if not self._admin_mode:
            return
        self._refresh_state()
        if not self._status.valid_backup_exists:
            self._show_message("백업 삭제", "정상 백업 이미지가 없습니다.")
            return
        if not self._require_helper():
            return
        if not self._confirm(
            title="백업 이미지 삭제",
            body="현재 복구 백업이 삭제됩니다.\n\n계속하시겠습니까?",
            accept_label="삭제",
            message_type=Gtk.MessageType.WARNING,
        ):
            self._set_operation_message("백업 삭제가 취소되었습니다. 관리자 메뉴로 돌아갑니다.")
            return
        if not self._confirm(
            title="백업 삭제 확인",
            body=(
                "경고: 백업 이미지가 RECOVERY_IMAGE에서 영구적으로 삭제됩니다.\n"
                "이 작업은 되돌릴 수 없습니다.\n\n"
                "지금 백업 이미지를 삭제하시겠습니까?"
            ),
            accept_label="삭제",
            message_type=Gtk.MessageType.WARNING,
        ):
            self._set_operation_message("백업 삭제가 취소되었습니다. 관리자 메뉴로 돌아갑니다.")
            return
        self._run_worker(
            start_message="백업 이미지 삭제 중...",
            worker=self._delete_worker,
        )

    def _delete_worker(self) -> OperationResult:
        rc, payload, stderr = run_runtime_admin_json("delete-backup")
        if rc != 0 or payload.get("status") != "COMPLETED":
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return OperationResult(False, "백업 삭제 실패", f"백업 삭제 실패: {reason}")
        return OperationResult(True, "백업 삭제 완료", "백업 이미지가 삭제되었습니다.\n관리자 메뉴로 돌아갑니다.")

    def _on_reboot_clicked(self, button: Gtk.Button) -> None:
        if button is self._exit_btn:
            self._request_pc_reboot()
            return
        self._request_reboot_to_windows(
            title="Windows로 재부팅",
            body="Recoverix를 종료하고 이 컴퓨터를 Windows로 재부팅합니다.\n\n계속하시겠습니까?",
        )

    def _request_pc_reboot(self) -> bool:
        if self._busy:
            self._show_message(
                "작업 진행 중",
                "현재 작업이 끝난 뒤 재부팅하세요.",
                message_type=Gtk.MessageType.WARNING,
            )
            return False
        if not self._require_helper():
            return False
        if not self._confirm(
            "PC 재부팅",
            (
                "현재 복구 환경을 종료하고 컴퓨터를 다시 시작합니다.\n"
                "다음 부팅 대상은 시스템 부팅 순서에 따라 결정됩니다.\n\n"
                "계속하시겠습니까?"
            ),
            accept_label="재부팅",
            message_type=Gtk.MessageType.WARNING,
        ):
            self._set_operation_message("재부팅이 취소되었습니다.")
            return False
        self._run_worker(
            start_message="PC 재부팅 요청 중...",
            worker=self._pc_reboot_worker,
        )
        return True

    def _request_reboot_to_windows(self, *, title: str, body: str) -> bool:
        if self._busy:
            self._show_message(
                "작업 진행 중",
                "현재 작업이 끝난 뒤 재부팅하세요.",
                message_type=Gtk.MessageType.WARNING,
            )
            return False
        if not self._require_helper():
            return False
        if not self._confirm(
            title,
            body,
            accept_label="재부팅",
            message_type=Gtk.MessageType.WARNING,
        ):
            self._set_operation_message("재부팅이 취소되었습니다.")
            return False
        self._run_worker(
            start_message="Windows 재부팅 요청 중...",
            worker=self._reboot_worker,
        )
        return True

    def _reboot_worker(self) -> OperationResult:
        rc, payload, stderr = run_runtime_admin_json("reboot-windows")
        if rc != 0 or payload.get("status") != "COMPLETED":
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return OperationResult(False, "재부팅 실패", f"Windows 재부팅 실패: {reason}")
        return OperationResult(True, "재부팅 요청 완료", "Windows 재부팅을 요청했습니다.")

    def _pc_reboot_worker(self) -> OperationResult:
        rc, payload, stderr = run_runtime_admin_json("reboot-pc")
        if rc != 0 or payload.get("status") != "COMPLETED":
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return OperationResult(False, "재부팅 실패", f"PC 재부팅 실패: {reason}")
        return OperationResult(True, "재부팅 요청 완료", "PC 재부팅을 요청했습니다.")

    def _on_windows_extend_clicked(self, _button: Gtk.Button) -> None:
        if not self._admin_mode or not self._require_helper():
            return
        if not self._confirm(
            title="복원 파티션 준비",
            body=(
                "Windows 파티션 경계를 복원 가능한 상태로 준비할 수 있는지 확인합니다.\n\n"
                "이 확인 단계에서는 파티션이 변경되지 않습니다.\n\n"
                "계속하시겠습니까?"
            ),
            accept_label="확인",
        ):
            self._set_operation_message("복원 파티션 준비가 취소되었습니다. 관리자 메뉴로 돌아갑니다.")
            return
        self._run_worker(
            start_message="Windows 파티션 경계 확인 중...",
            worker=self._plan_windows_extend_worker,
            on_done=self._handle_windows_extend_plan,
        )

    def _plan_windows_extend_worker(self) -> OperationResult:
        rc, payload, stderr = run_runtime_admin_json("plan-windows-extend")
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        if rc != 0 or payload.get("status") != "COMPLETED":
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return OperationResult(False, "파티션 준비 실패", reason, payload=payload)
        return OperationResult(True, "파티션 준비 계획", self._format_windows_extend_plan(plan), payload=plan)

    def _handle_windows_extend_plan(self, result: OperationResult) -> None:
        self._refresh_state()
        if not result.ok:
            self._show_message(result.title, result.body, message_type=Gtk.MessageType.ERROR)
            return
        plan = result.payload or {}
        status = str(plan.get("status") or "UNKNOWN")
        can_extend = bool(plan.get("can_extend"))
        if status == "READY":
            self._set_operation_message("Windows 파티션이 이미 복원에 충분한 크기입니다.")
            self._set_notes_text(result.title, result.body)
            return
        if not can_extend:
            self._set_operation_message("Windows 파티션을 안전하게 준비할 수 없습니다.")
            self._set_notes_text(result.title, result.body)
            return
        if not self._confirm(
            title="Windows 파티션 준비",
            body=(
                f"{result.body}\n\n"
                "이 작업은 Windows 파티션 경계만 바로 뒤의 연속된 빈 공간으로 확장합니다. "
                "NTFS 파일시스템 내부 크기는 변경하지 않고, 다른 파티션도 이동하지 않습니다.\n\n"
                "계속하시겠습니까?"
            ),
            accept_label="준비",
            message_type=Gtk.MessageType.WARNING,
        ):
            self._set_operation_message("복원 파티션 준비가 취소되었습니다. 관리자 메뉴로 돌아갑니다.")
            return
        self._run_worker(
            start_message="Windows 파티션 경계 준비 중...",
            worker=self._run_windows_extend_worker,
        )

    def _run_windows_extend_worker(self) -> OperationResult:
        rc, payload, stderr = run_runtime_admin_json("run-windows-extend")
        status = payload.get("status")
        if status == "COMPLETED":
            return OperationResult(
                True,
                "파티션 준비 완료",
                "Windows 파티션 경계가 복원 가능한 상태로 준비되었습니다.\n\n시스템 복원을 다시 실행하세요.",
                payload=payload,
            )
        if status == "PENDING_REBOOT":
            reason = str(payload.get("reason") or "시스템이 아직 새 파티션 크기를 감지하지 못했습니다.")
            next_step = str(
                payload.get("next_step")
                or "컴퓨터를 재시작한 뒤 복원 파티션 준비를 다시 실행하세요."
            )
            return OperationResult(
                False,
                "파티션 준비 대기",
                f"{reason}\n\n{next_step}",
                payload=payload,
            )
        reason = str(payload.get("reason") or stderr or status or rc)
        return OperationResult(False, "파티션 준비 실패", reason, payload=payload)

    def _format_windows_extend_plan(self, plan: dict[str, Any]) -> str:
        rows = [
            ("상태", plan.get("status") or "UNKNOWN"),
            ("사유", plan.get("reason") or "-"),
            ("Windows 파티션", plan.get("windows_partition") or "-"),
            ("현재 크기", self._format_bytes(_to_int(plan.get("current_size_bytes")))),
            ("필요 크기", self._format_bytes(_to_int(plan.get("required_size_bytes")))),
            ("복원 여유분", self._format_bytes(_to_int(plan.get("restore_margin_bytes")))),
            ("C 뒤 빈 공간", self._format_bytes(_to_int(plan.get("available_after_bytes")))),
            ("목표 크기", self._format_bytes(_to_int(plan.get("target_size_bytes")))),
            ("다음 파티션", plan.get("next_partition") or "-"),
            ("확장 가능", _ko_bool(bool(plan.get("can_extend")))),
        ]
        longest = max(len(name) for name, _ in rows)
        return "\n".join(f"{name:<{longest}} : {value}" for name, value in rows)

    def _on_clear_lock_clicked(self, _button: Gtk.Button) -> None:
        if not self._admin_mode or not self._require_helper():
            return
        if not self._confirm(
            title="복원 실패 잠금 해제",
            body=(
                "백업 데이터는 유지한 상태로 복원 실패 잠금만 해제합니다.\n\n"
                "계속하시겠습니까?"
            ),
            accept_label="잠금 해제",
        ):
            self._set_operation_message("복원 실패 잠금 해제가 취소되었습니다.")
            return
        self._run_worker(
            start_message="복원 실패 잠금 해제 중...",
            worker=self._clear_lock_worker,
        )

    def _clear_lock_worker(self) -> OperationResult:
        rc, payload, stderr = run_runtime_admin_json("clear-restore-failure-lock")
        if rc != 0 or payload.get("status") != "COMPLETED":
            reason = str(payload.get("reason") or stderr or payload.get("status") or rc)
            return OperationResult(False, "잠금 해제 실패", reason, payload=payload)
        return OperationResult(True, "복원 실패 잠금 해제 완료", "복원 실패 잠금이 해제되었습니다.")

    def _on_boot_status_clicked(self, _button: Gtk.Button) -> None:
        self._refresh_state()
        body = self._build_status_summary()
        try:
            boot = subprocess.run(
                ["efibootmgr", "-v"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            firmware = (
                "펌웨어 부팅 항목\n\n"
                "상태: 조회 실패\n"
                f"사유: efibootmgr 실행 실패: {exc}"
            )
        else:
            firmware = self._build_firmware_boot_summary(
                returncode=boot.returncode,
                stdout=boot.stdout,
                stderr=boot.stderr,
            )
        self._set_operation_message("부팅 및 복구 상태를 정보 패널에 표시했습니다.")
        self._set_notes_text("부팅/복구 상태", f"{body}\n\n{firmware}")

    def _on_logs_clicked(self, _button: Gtk.Button) -> None:
        self._refresh_state()
        self._set_operation_message("진단 로그를 정보 패널에 표시했습니다.")
        self._set_notes_text("진단 로그", self._collect_log_excerpt())

    def _collect_log_excerpt(self) -> str:
        candidates = [
            Path("/var/log/recoverix-ui.log"),
            Path("/var/log/recoverix-image-status.log"),
            Path("/var/log/recoverix-restore-preflight.log"),
            Path("/var/log/recoverix-restore-plan.log"),
            Path("/tmp/recoverix-gui-service.log"),
        ]
        if self._status.mount_point is not None:
            log_root = logs_dir(self._status.mount_point)
            candidates.extend(sorted(log_root.glob("*.log"))[:10])

        chunks: list[str] = []
        for path in candidates:
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            excerpt = "\n".join(lines[-80:])
            chunks.append(f"===== {path} =====\n{excerpt}")
        return "\n\n".join(chunks) if chunks else "진단 로그를 찾을 수 없습니다."

def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def run_recovery_ui(
    *,
    marker: Path | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> int:
    """Create main window and enter GTK main loop."""
    init_result = Gtk.init_check(None)
    init_ok = bool(init_result[0]) if isinstance(init_result, tuple) else init_result is not None
    if not init_ok:
        ui_log("UI start failed: Gtk.init_check failed")
        return 1

    logger = log_fn or ui_log
    display = os.environ.get("DISPLAY", "")
    xdg_session_type = os.environ.get("XDG_SESSION_TYPE", "")
    logger(f"startup env DISPLAY={display!r} XDG_SESSION_TYPE={xdg_session_type!r}")
    logger(f"startup gtk_version={gtk_version_summary()}")

    window = RecoveryWindow(marker=marker, log_fn=log_fn)
    logger("window created")
    logger("show_all called")
    window.show_all()
    window.fullscreen()
    try:
        logger(f"window visible state after show_all: visible={window.get_visible()}")
    except Exception:  # noqa: BLE001
        logger("window visible state after show_all: visible=<unknown>")

    try:
        logger("present called")
        window.present()
    except Exception as exc:  # noqa: BLE001
        logger(f"present failed: {exc}")

    def _idle_present() -> bool:
        try:
            logger("present called (idle)")
            window.present()
        except Exception as exc:  # noqa: BLE001
            logger(f"present failed (idle): {exc}")
        return False

    GLib.idle_add(_idle_present)

    logger("Gtk.main entered")
    Gtk.main()
    return 0

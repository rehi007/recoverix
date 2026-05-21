"""TUI helpers for Recovery Runtime (keyboard-only, no desktop GUI)."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from restore_engine.restore_state import logs_dir

DELETE_CONFIRMATION_PHRASE = "DELETE BACKUP DATA"
BACKUP_CONFIRMATION_PHRASE = "START BACKUP"
LOG_PAGE_LINES = 20

InputFunc = Callable[[str], str]


def default_input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def append_error_log(recovery_root: Path, message: str) -> None:
    """Record UI/action errors without affecting operation success semantics."""
    try:
        log_dir = logs_dir(recovery_root)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "error.log"
        stamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | {message}\n")
    except OSError:
        pass


def prompt_yes_no(
    question: str,
    *,
    input_func: InputFunc = default_input,
) -> bool:
    answer = input_func(f"{question} [y/N]> ").lower()
    return answer in ("y", "yes")


def prompt_phrase(
    expected: str,
    *,
    description: str,
    input_func: InputFunc = default_input,
) -> str:
    print(description)
    print(f'필요한 확인 문구: "{expected}"')
    return input_func("confirmation phrase> ").strip()


def verify_phrase(phrase: Optional[str], expected: str) -> bool:
    if phrase is None:
        return False
    return phrase.strip() == expected


def format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024**2:
        return f"{value / 1024:.1f} KiB"
    if value < 1024**3:
        return f"{value / 1024**2:.1f} MiB"
    return f"{value / 1024**3:.2f} GiB"


def tail_log_file(
    path: Path,
    *,
    lines: int = LOG_PAGE_LINES,
    page: int = 0,
) -> tuple[List[str], bool]:
    """Return a page of log lines (newest chunk first page)."""
    if not path.is_file():
        return [], False
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], False
    if not content:
        return [], False
    start = max(0, len(content) - (page + 1) * lines)
    end = max(0, len(content) - page * lines)
    chunk = content[start:end]
    has_older = start > 0
    return chunk, has_older


def render_log_menu(
    recovery_root: Path,
    log_names: Sequence[str],
    *,
    input_func: InputFunc = default_input,
) -> str:
    """Interactive log viewer with simple paging."""
    available = []
    for name in log_names:
        path = logs_dir(recovery_root) / name
        if path.is_file():
            available.append((name, path))
    if not available:
        return "표시할 로그 파일이 없습니다."

    print("\n로그 파일:")
    for index, (name, _path) in enumerate(available, start=1):
        print(f"  {index}. {name}")
    print("  0. 돌아가기")
    choice = input_func("로그 선택> ").strip()
    if choice in ("0", ""):
        return "로그 보기를 종료합니다."
    try:
        selected = int(choice) - 1
    except ValueError:
        return f"잘못된 선택: {choice!r}"
    if selected < 0 or selected >= len(available):
        return f"잘못된 선택: {choice!r}"

    name, path = available[selected]
    page = 0
    while True:
        chunk, has_older = tail_log_file(path, page=page)
        print(f"\n--- {name} (page {page}) ---")
        if not chunk:
            print("(empty)")
        else:
            for line in chunk:
                print(line)
        print("---")
        if has_older:
            nav = input_func("[n]ext older / [q]uit> ").strip().lower()
            if nav == "n":
                page += 1
                continue
        return f"{name} 로그를 표시했습니다."

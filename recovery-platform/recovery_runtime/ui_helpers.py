"""TUI helpers for Recovery Runtime (keyboard-only, no desktop GUI)."""

from __future__ import annotations

from contextlib import contextmanager
import shutil
import sys
import termios
import tty
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence

from restore_engine.restore_state import logs_dir

DELETE_CONFIRMATION_PHRASE = "DELETE BACKUP DATA"
BACKUP_CONFIRMATION_PHRASE = "START BACKUP"
LOG_PAGE_LINES = 20
LOG_VIEW_WIDTH = 76
LOG_VIEW_INDENT = "    "
LOG_VIEW_RULE = "=" * 60
LOG_MENU_RETURN = "__RECOVERIX_LOG_MENU_RETURN__"
_CLEAR_SCREEN = "\033[H\033[2J\033[3J"

InputFunc = Callable[[str], str]
_TTY_FALLBACK_PATHS = ("/dev/tty", "/dev/tty1")


def _key_input_attrs(fd: int, *, min_chars: int = 1) -> list:
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    attrs[6][termios.VMIN] = min_chars
    attrs[6][termios.VTIME] = 0
    return attrs


def flush_tty_input() -> None:
    """Drop pending boot/menu keystrokes without printing them."""
    if sys.stdin.isatty():
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except termios.error:
            pass

    for tty_path in _TTY_FALLBACK_PATHS:
        try:
            with open(tty_path, "r+b", buffering=0) as handle:
                termios.tcflush(handle.fileno(), termios.TCIFLUSH)
        except (OSError, termios.error):
            continue


@contextmanager
def suppress_tty_input() -> Iterator[None]:
    handle = None
    old = None
    try:
        for tty_path in _TTY_FALLBACK_PATHS:
            try:
                handle = open(tty_path, "r+b", buffering=0)
                break
            except OSError:
                continue
        if handle is None:
            yield
            return

        fd = handle.fileno()
        try:
            old = termios.tcgetattr(fd)
        except termios.error:
            yield
            return
        new = _key_input_attrs(fd, min_chars=0)
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        yield
    finally:
        if handle is not None:
            fd = handle.fileno()
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except termios.error:
                pass
            if old is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                except termios.error:
                    pass
            handle.close()


def _read_tty_char(prompt: str) -> str:
    for tty_path in _TTY_FALLBACK_PATHS:
        try:
            with open(tty_path, "r+b", buffering=0) as handle:
                fd = handle.fileno()
                try:
                    old = termios.tcgetattr(fd)
                except termios.error:
                    continue
                try:
                    handle.write(prompt.encode("utf-8", errors="replace"))
                    termios.tcsetattr(fd, termios.TCSADRAIN, _key_input_attrs(fd))
                    while True:
                        raw = handle.read(1)
                        if not raw:
                            break
                        char = raw.decode("utf-8", errors="ignore")
                        if char in ("\n", "\r"):
                            continue
                        handle.write(raw + b"\n")
                        return char
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except OSError:
            continue
    return ""


def _read_tty_yes_no() -> str:
    if sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSADRAIN, _key_input_attrs(fd))
            while True:
                raw = sys.stdin.buffer.read(1)
                if not raw:
                    break
                char = raw.decode("utf-8", errors="ignore").lower()
                if char not in ("y", "n"):
                    continue
                sys.stdout.write(raw.decode("utf-8", errors="replace") + "\n")
                sys.stdout.flush()
                return char
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    for tty_path in _TTY_FALLBACK_PATHS:
        try:
            with open(tty_path, "r+b", buffering=0) as handle:
                fd = handle.fileno()
                try:
                    old = termios.tcgetattr(fd)
                except termios.error:
                    continue
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)
                    termios.tcsetattr(fd, termios.TCSADRAIN, _key_input_attrs(fd))
                    while True:
                        raw = handle.read(1)
                        if not raw:
                            break
                        char = raw.decode("utf-8", errors="ignore").lower()
                        if char not in ("y", "n"):
                            continue
                        sys.stdout.write(raw.decode("utf-8", errors="replace") + "\n")
                        sys.stdout.flush()
                        return char
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except OSError:
            continue
    return ""


def _read_tty_choice(valid_choices: set[str], *, prompt: str = "") -> str:
    normalized = {choice.lower() for choice in valid_choices}
    if prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    if sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSADRAIN, _key_input_attrs(fd))
            while True:
                raw = sys.stdin.buffer.read(1)
                if not raw:
                    break
                char = raw.decode("utf-8", errors="ignore")
                choice = char.lower()
                if choice not in normalized:
                    continue
                sys.stdout.write(char + "\n")
                sys.stdout.flush()
                return choice
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    for tty_path in _TTY_FALLBACK_PATHS:
        try:
            with open(tty_path, "r+b", buffering=0) as handle:
                fd = handle.fileno()
                try:
                    old = termios.tcgetattr(fd)
                except termios.error:
                    continue
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)
                    termios.tcsetattr(fd, termios.TCSADRAIN, _key_input_attrs(fd))
                    while True:
                        raw = handle.read(1)
                        if not raw:
                            break
                        char = raw.decode("utf-8", errors="ignore")
                        choice = char.lower()
                        if choice not in normalized:
                            continue
                        sys.stdout.write(char + "\n")
                        sys.stdout.flush()
                        return choice
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except OSError:
            continue
    return ""


def _clear_screen() -> None:
    cleared = False
    try:
        sys.stdout.write(_CLEAR_SCREEN)
        sys.stdout.flush()
        cleared = True
    except Exception:
        pass
    for tty_path in _TTY_FALLBACK_PATHS:
        try:
            with open(tty_path, "wb", buffering=0) as handle:
                handle.write(_CLEAR_SCREEN.encode("ascii"))
                cleared = True
                break
        except OSError:
            continue
    if cleared:
        try:
            sys.stdout.flush()
        except Exception:
            pass


def _read_tty_line(prompt: str) -> str:
    for tty_path in _TTY_FALLBACK_PATHS:
        try:
            with open(tty_path, "r+b", buffering=0) as handle:
                fd = handle.fileno()
                old = termios.tcgetattr(fd)
                try:
                    handle.write(prompt.encode("utf-8", errors="replace"))
                    tty.setcbreak(fd)
                    buffer = bytearray()
                    while True:
                        raw = handle.read(1)
                        if not raw:
                            break
                        char = raw.decode("utf-8", errors="ignore")
                        if char in ("\n", "\r"):
                            handle.write(b"\n")
                            return buffer.decode("utf-8", errors="replace").strip()
                        if char == "\x7f":
                            if buffer:
                                buffer.pop()
                                handle.write(b"\b \b")
                            continue
                        buffer.extend(raw)
                        handle.write(raw)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except OSError:
            continue
    return ""


def default_input(prompt: str) -> str:
    if sys.stdin.isatty():
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    line = _read_tty_line(prompt)
    if line != "":
        return line

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
    prompt = f"{question} [y/n]> "
    sys.stdout.write(prompt)
    sys.stdout.flush()
    answer = _read_tty_yes_no()
    if answer:
        return answer == "y"

    while True:
        answer = input_func("").strip().lower()
        if answer in ("n", "no"):
            return False
        if answer in ("y", "yes"):
            return True


def prompt_phrase(
    expected: str,
    *,
    description: str,
    input_func: InputFunc = default_input,
) -> str:
    print(description)
    print(f'Required confirmation phrase: "{expected}"')
    sys.stdout.write("confirmation phrase> ")
    sys.stdout.flush()
    line = _read_tty_line("")
    if line != "":
        return line.strip()
    return input_func("").strip()


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
    log_names: Sequence[str | tuple[str, Path]],
    *,
    input_func: InputFunc = default_input,
) -> str:
    """Interactive log viewer with simple paging."""
    available = []
    for entry in log_names:
        if isinstance(entry, tuple):
            name, path = entry
        else:
            name = entry
            path = logs_dir(recovery_root) / entry
        if path.is_file():
            available.append((name, path))
    if not available:
        return "No log files are available."

    terminal = shutil.get_terminal_size(fallback=(80, 24))
    term_width = terminal.columns
    term_height = terminal.lines
    content_width = max(40, min(LOG_VIEW_WIDTH, term_width - 2))
    log_page_lines = max(5, min(LOG_PAGE_LINES, term_height - 12))

    while True:
        _clear_screen()
        print(LOG_VIEW_RULE)
        print(" Diagnostics Logs")
        print(LOG_VIEW_RULE)
        print(" Open runtime and recovery logs.")
        print()
        print(" Available logs")
        for index, (name, _path) in enumerate(available, start=1):
            print(f"{LOG_VIEW_INDENT}{index}. {name}")
        print(f"{LOG_VIEW_INDENT}0. Return")
        print()
        valid_choices = {"0", *(str(index) for index in range(1, len(available) + 1))}
        choice = _read_tty_choice(
            valid_choices,
            prompt=f"{LOG_VIEW_INDENT}Log selection> ",
        )
        while choice == "":
            choice = input_func("").strip().lower()
            if choice not in valid_choices:
                choice = ""
        if choice == "0":
            return LOG_MENU_RETURN
        selected = int(choice) - 1

        name, path = available[selected]
        page = 0
        while True:
            _clear_screen()
            chunk, has_older = tail_log_file(path, lines=log_page_lines, page=page)
            print(LOG_VIEW_RULE)
            print(" Diagnostics Logs")
            print(LOG_VIEW_RULE)
            print(f" Viewing log: {name}")
            print()
            print(f"{LOG_VIEW_INDENT}--- {name} (page {page}) ---")
            if not chunk:
                print(f"{LOG_VIEW_INDENT}(empty)")
            else:
                for line in chunk:
                    print(f"{LOG_VIEW_INDENT}{_fit_log_line(line, content_width)}")
            print(f"{LOG_VIEW_INDENT}---")
            if has_older:
                print()
                print(f"{LOG_VIEW_INDENT}1. Older page")
                print(f"{LOG_VIEW_INDENT}0. Return")
                print()
                valid_nav = {"0", "1"}
            else:
                print()
                print(f"{LOG_VIEW_INDENT}0. Return")
                print()
                valid_nav = {"0"}
            nav = _read_tty_choice(valid_nav, prompt=f"{LOG_VIEW_INDENT}Log action> ")
            while nav == "":
                nav = input_func("").strip().lower()
                if nav not in valid_nav:
                    nav = ""
            if has_older and nav == "1":
                page += 1
                continue
            if nav == "0":
                break


def _fit_log_line(line: str, width: int) -> str:
    text = line.rstrip()
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."

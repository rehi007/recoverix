#!/usr/bin/env python3
"""Linux Recovery Runtime entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone
from time import perf_counter

from common.logger import get_logger, setup_logging

logger = get_logger(__name__)

_LOG_PATH = Path("/var/log/recovery-runtime.log")
_FALLBACK_LOG_PATH = Path("/tmp/recovery-runtime.log")
_BOOTSTRAP_LOG_PATH = Path("/tmp/recovery-runtime-bootstrap.log")


def _append_bootstrap_log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        _BOOTSTRAP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _BOOTSTRAP_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | {message}\n")
    except OSError:
        pass


def _resolve_log_file() -> Path | None:
    if sys.platform != "linux":
        return None
    for candidate in (_LOG_PATH, _FALLBACK_LOG_PATH):
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with candidate.open("a", encoding="utf-8"):
                pass
            return candidate
        except OSError:
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    started = perf_counter()
    _append_bootstrap_log("main: enter")
    setup_logging(log_file=_resolve_log_file(), console=False)
    _append_bootstrap_log("main: logging configured")
    parser = argparse.ArgumentParser(description="Linux Recovery Runtime")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Show status once and exit (for tests/CI)",
    )
    args = parser.parse_args(argv)

    if sys.platform == "win32":
        print("Recovery Runtime cannot run on Windows", file=sys.stderr)
        return 1

    from recovery_runtime.menu import run_menu_loop
    from recovery_runtime.runtime_context import build_runtime_context

    if sys.platform != "linux":
        print("Recovery Runtime cannot run on Windows", file=sys.stderr)
        return 1

    try:
        from recovery_runtime.discover import require_linux

        require_linux()
        _append_bootstrap_log("main: require_linux ok")
    except RuntimeError as exc:
        logger.error(str(exc))
        _append_bootstrap_log(f"main: require_linux failed: {exc}")
        print(exc, file=sys.stderr)
        return 1

    logger.info("starting recovery runtime")
    try:
        ctx = build_runtime_context()
        _append_bootstrap_log("main: runtime context built")
    except Exception as exc:
        logger.exception("failed to build runtime context")
        _append_bootstrap_log(f"main: runtime context failed: {exc}")
        print(f"runtime bootstrap failed: {exc}", file=sys.stderr)
        return 1

    if args.non_interactive:
        from recovery_runtime.actions import show_status_action

        _append_bootstrap_log("main: non-interactive status path")
        print(show_status_action(ctx))
        return 0

    _append_bootstrap_log("main: entering menu loop")
    _append_bootstrap_log(f"main: pre-menu total {(perf_counter() - started) * 1000.0:.1f}ms")
    return run_menu_loop(ctx)


if __name__ == "__main__":
    raise SystemExit(main())

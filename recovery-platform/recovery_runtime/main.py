#!/usr/bin/env python3
"""Linux Recovery Runtime entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common.logger import get_logger, setup_logging

logger = get_logger(__name__)

_LOG_PATH = Path("/var/log/recovery-runtime.log")
_FALLBACK_LOG_PATH = Path("/tmp/recovery-runtime.log")


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
    setup_logging(log_file=_resolve_log_file())
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
    except RuntimeError as exc:
        logger.error(str(exc))
        print(exc, file=sys.stderr)
        return 1

    logger.info("starting recovery runtime")
    try:
        ctx = build_runtime_context()
    except Exception as exc:
        logger.exception("failed to build runtime context")
        print(f"runtime bootstrap failed: {exc}", file=sys.stderr)
        return 1

    if args.non_interactive:
        from recovery_runtime.actions import show_status_action

        print(show_status_action(ctx))
        return 0

    return run_menu_loop(ctx)


if __name__ == "__main__":
    raise SystemExit(main())

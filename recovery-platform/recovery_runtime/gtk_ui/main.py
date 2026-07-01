"""Entry point: python3 -m recovery_runtime.gtk_ui.main"""

from __future__ import annotations

import sys

from recovery_runtime.gtk_ui.window import run_recovery_ui


def main(argv: list[str] | None = None) -> int:
    _ = argv if argv is not None else sys.argv[1:]
    return run_recovery_ui()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Package release/dist into zip/tar.gz."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "release" / "dist"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tar/zip packaging for release/dist")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Bundle root")
    parser.add_argument(
        "--name",
        type=str,
        default="recovery-boot-platform",
        help="Archive base filename (no extension)",
    )
    parser.add_argument(
        "--formats",
        type=str,
        default="zip,gztar",
        help="Comma list for shutil.make_archive (zip,gztar)",
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"missing bundle {args.root}", file=sys.stderr)
        return 1

    stem = ROOT / "release" / args.name

    fmts = [f.strip() for f in args.formats.split(",") if f.strip()]
    for fmt in fmts:
        arc = shutil.make_archive(str(stem), fmt, root_dir=str(args.root))
        print(arc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

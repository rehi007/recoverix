#!/usr/bin/env python3
"""Validate release bundle contents (FAIL CLOSED on missing required files)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = ROOT / "release" / "dist"

REQUIRED_EFI_FILES = ("shimx64.efi", "grubx64.efi", "grub.cfg")


def _fail(msg: str) -> None:
    print(f"validate_release: FAIL {msg}", file=sys.stderr)


def validate_bundle(bundle_root: Path, *, strict_placeholder: bool) -> int:
    ok = True
    mf_path = bundle_root / "manifests" / "product_manifest.json"
    if not mf_path.is_file():
        _fail(f"missing {mf_path}")
        return 2
    prod = json.loads(mf_path.read_text(encoding="utf-8"))

    for doc in prod.get("required_docs") or []:
        path = bundle_root / "docs" / doc
        if not path.is_file():
            _fail(f"missing doc {path}")
            ok = False

    efi_dir = bundle_root / "efi"
    for name in REQUIRED_EFI_FILES:
        if not (efi_dir / name).is_file():
            _fail(f"missing EFI artefact {efi_dir / name}")
            ok = False

    for rel in ("recovery_runtime/main.py", "windows_agent/agent.py"):
        if not (bundle_root / rel).is_file():
            _fail(f"missing runtime file {bundle_root / rel}")
            ok = False

    if not (bundle_root / "manifests" / "bundle_manifest.json").is_file():
        _fail("missing manifests/bundle_manifest.json")
        ok = False

    if strict_placeholder:
        tokens = list(prod.get("placeholder_tokens") or [])
        text = mf_path.read_text(encoding="utf-8")
        for t in tokens:
            if t in text:
                _fail(f"placeholder token '{t}' in manifest (--strict)")
                ok = False
        ver = str(prod.get("version") or "")
        if ver.endswith("-dev") or ver == "0.0.0-dev":
            _fail("development version not allowed under --strict")
            ok = False

    print("validate_release: PASS" if ok else "validate_release: FAIL")
    return 0 if ok else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate release bundle layout")
    parser.add_argument("--root", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Reject dev version / placeholder_tokens in shipped manifest",
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        _fail(f"bundle root not found {args.root}")
        return 2

    return validate_bundle(args.root, strict_placeholder=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())

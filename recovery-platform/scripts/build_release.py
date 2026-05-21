#!/usr/bin/env python3
"""Populate release/dist with product bundle (sources only; runs on dev host)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "release" / "dist"


def _load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".git"),
    )


def build_release(*, out: Path, manifest_path: Path) -> None:
    manifest = _load_manifest(manifest_path)
    pkgs = list(manifest.get("packages") or [])
    docs = list(manifest.get("required_docs") or [])

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    docs_dir = out / "docs"
    docs_dir.mkdir()
    for name in docs:
        src_doc = ROOT / "docs" / name
        if not src_doc.is_file():
            raise FileNotFoundError(f"missing source doc {src_doc}")
        shutil.copy2(src_doc, docs_dir / name)

    efi_src = ROOT / "boot_manager" / "assets" / "recovery_boot"
    efi_dst = out / "efi"
    _copy_tree(efi_src, efi_dst)

    for pkg in pkgs:
        src = ROOT / pkg
        if not src.is_dir():
            raise FileNotFoundError(f"missing package {pkg}")
        _copy_tree(src, out / pkg)

    bundle_meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "product_id": manifest.get("product_id"),
        "product_name": manifest.get("product_name"),
        "version": manifest.get("version"),
        "source_repo_root": str(ROOT),
    }
    manifests = out / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "bundle_manifest.json").write_text(json.dumps(bundle_meta, indent=2), encoding="utf-8")
    shutil.copy2(manifest_path, manifests / "product_manifest.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build release/dist bundle")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT, help="Output directory")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "config" / "product_manifest.json",
    )
    args = parser.parse_args(argv)

    try:
        build_release(out=args.output, manifest_path=args.manifest)
    except Exception as exc:  # noqa: BLE001
        print(f"build_release failed: {exc}", file=sys.stderr)
        return 1

    print(f"Release bundle written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

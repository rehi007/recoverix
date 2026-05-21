#!/usr/bin/env python3
"""저장소 트리·파일/모듈/테스트·문서 수 요약(읽기 전용)."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
        "node_modules",
    }
)


def analyze(root: Path, *, include_release_dist: bool) -> dict:
    root = root.resolve()
    file_by_ext: dict[str, int] = defaultdict(int)
    py_files = 0
    test_py = 0
    docs_md = 0
    tools_py = 0
    dir_walked = 0

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dpath = Path(dirpath)
        dirnames[:] = sorted(dn for dn in dirnames if dn not in SKIP_DIR_NAMES)
        try:
            rel = dpath.relative_to(root)
        except ValueError:
            rel = Path(".")
        parts = rel.parts

        if not include_release_dist and len(parts) >= 2 and parts[0] == "release" and parts[1] == "dist":
            dirnames.clear()
            continue

        dir_walked += 1

        for fn in filenames:
            p = dpath / fn
            try:
                rp = p.relative_to(root)
            except ValueError:
                continue
            suf = p.suffix.lower()
            key = suf if suf else "(no_suffix)"
            file_by_ext[key] += 1
            if suf == ".py":
                py_files += 1
                srp = rp.as_posix()
                if srp.startswith("tests/"):
                    test_py += 1
                elif srp.startswith("tools/"):
                    tools_py += 1
            if len(rp.parts) >= 2 and rp.parts[0] == "docs" and rp.suffix.lower() == ".md":
                docs_md += 1

    module_dirs = sorted(
        d.name
        for d in root.iterdir()
        if d.is_dir() and all(p not in SKIP_DIR_NAMES for p in d.parts) and not d.name.startswith(".")
    )

    def tree_lines(max_depth: int = 3) -> list[str]:
        lines: list[str] = [f"{root.name}/"]

        def walk(p: Path, prefix: str, depth: int) -> None:
            if depth > max_depth:
                return
            if p.name in SKIP_DIR_NAMES:
                return
            try:
                kids = sorted([c for c in p.iterdir() if c.name not in SKIP_DIR_NAMES], key=lambda x: x.name)
            except OSError:
                return
            for i, c in enumerate(kids):
                if c.is_dir():
                    try:
                        cr = c.relative_to(root)
                    except ValueError:
                        cr = Path(c.name)
                    if (
                        not include_release_dist
                        and len(cr.parts) >= 2
                        and cr.parts[0] == "release"
                        and cr.parts[1] == "dist"
                    ):
                        is_last = i == len(kids) - 1
                        connector = "└── " if is_last else "├── "
                        lines.append(f"{prefix}{connector}{c.name}/")
                        continue
                is_last = i == len(kids) - 1
                connector = "└── " if is_last else "├── "
                ext = "" if not c.is_dir() else "/"
                lines.append(f"{prefix}{connector}{c.name}{ext}")
                if c.is_dir():
                    walk(c, prefix + ("    " if is_last else "│   "), depth + 1)

        walk(root, "", 0)
        return lines[:500]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "directory_tree_preview": tree_lines(3),
        "counts": {
            "directories_walked": dir_walked,
            "python_files": py_files,
            "test_python_files": test_py,
            "tools_python_files": tools_py,
            "markdown_docs_under_docs": docs_md,
            "files_by_extension": dict(sorted(file_by_ext.items())),
        },
        "top_level_directories": module_dirs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project tree and file-count report")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-release-dist", action="store_true", help="release/dist 디렉터리까지 순회 포함")
    args = parser.parse_args(argv)

    data = analyze(args.root.resolve(), include_release_dist=args.include_release_dist)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"project_tree_report: {data['repo_root']}")
        for line in data["directory_tree_preview"][:80]:
            print(line)
        print("---")
        print(json.dumps(data["counts"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

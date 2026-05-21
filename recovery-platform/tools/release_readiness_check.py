#!/usr/bin/env python3
"""프로젝트 릴리스 준비도(파괴 작업 없음) — 종합 판정: READY | READY_WITH_WARNINGS | NOT_READY."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "diagnostics" / "release_readiness.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.release_gate_lib import (  # noqa: E402
    DEFAULT_BUNDLE,
    build_readiness_payload,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release readiness aggregation (non-destructive)")
    parser.add_argument(
        "--bundle",
        type=Path,
        default=DEFAULT_BUNDLE,
        help="validate_release 검사 대상 번들 디렉터리 (기본: release/dist)",
    )
    parser.add_argument("--json", action="store_true", help="표준 출력에 최종 리포트 JSON 출력")
    args = parser.parse_args(argv)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = build_readiness_payload(REPO_ROOT, bundle=args.bundle)
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    tier = payload["readiness"]
    print(f"release_readiness_check: {tier}")
    print(f"  release_stage (권장): {payload['status']}")
    print(f"  pre_destructive_gate: {payload['pre_destructive_gate']}")
    print(f"  validate_release: ", end="")
    vr = next((s for s in payload["signals"] if s["id"] == "validate_release"), None)
    print(vr["status"] if vr else "MISSING")
    print(f"Wrote {OUT_PATH}")

    if tier == "NOT_READY":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

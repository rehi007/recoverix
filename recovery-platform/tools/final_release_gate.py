#!/usr/bin/env python3
"""최종 게이트: 파괴적 테스트 또는 release 패키징 직전 승인(호스트 레이아웃 포함)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "diagnostics" / "final_release_gate.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.release_gate_lib import (  # noqa: E402
    DEFAULT_BUNDLE,
    final_gate_payload,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Final release / destructive-test packaging gatekeeper")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE, help="validate_release 검사 번들 경로")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = final_gate_payload(REPO_ROOT, bundle=args.bundle)
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    print(f"final_release_gate: {payload['overall']}")
    print(payload["message"])
    print(f"Wrote {OUT_PATH}")

    return 0 if payload["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

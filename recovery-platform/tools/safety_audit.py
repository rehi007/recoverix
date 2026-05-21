#!/usr/bin/env python3
"""Aggregate static safety audit: pattern scan, policy checks, destructive command gates."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional, Sequence, Tuple

from tools.code_policy_audit import run_code_policy_audit, serialize as serialize_policy
from tools.destructive_command_audit import run_destructive_audit, serialize as serialize_destructive

AuditBucket = Literal["SKIP", "WARN_ONLY", "CODE"]


def _repo_root(script: Path | None = None) -> Path:
    return Path(script or Path(__file__)).resolve().parents[1]


REPO_ROOT = _repo_root()

DEFAULT_CONFIG = REPO_ROOT / "tools" / "forbidden_audit_patterns.json"
DEFAULT_REPORT_PATH = REPO_ROOT / "diagnostics" / "safety_audit_report.json"

_SCAN_SUFFIXES = {".py", ".md", ".txt", ".sh", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini"}
_EXCLUDE_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "release", "diagnostics"}

# 실행 코드 내에서 매치 시 FAIL 후보가 되는 추가 규칙 (문자열/주석 포함 — 감사 도구는 설명용이므로 SKIP)
_EXTRA_CODE_FAIL_RULES: Tuple[Dict[str, str], ...] = (
    {
        "id": "bcdedit_bootnext_force",
        "regex": r"(?i)bcdedit[^\n]{0,160}/set[^\n]{0,80}\s+bootnext\b",
        "label": "bcdedit /set … bootnext (BootNext 강제 의심)",
    },
    {
        "id": "format_partition_word",
        "regex": r"(?i)\bformat\s+partition\b",
        "label": "format partition 문구 (포맷 위험)",
    },
    {
        "id": "mkfs_command",
        "regex": r"(?i)\bmkfs\.",
        "label": "mkfs.* 유틸 직접 호출",
    },
)

def _load_json_config(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def classify_path(rel_unix: str, cfg: Dict[str, Any]) -> AuditBucket:
    exact: Sequence[str] = cfg.get("whitelist_exact_rel_paths", ())
    prefixes: Sequence[str] = cfg.get("whitelist_relative_path_prefixes", ())
    rel = rel_unix.replace("\\", "/").lstrip("./")

    if rel in exact:
        return "SKIP"

    excluded_prefixes_warn = tuple(prefixes)

    # 협업 문서 등: 예시 목적이면 코드와 동등 FAIL 대신 WARN
    for p in excluded_prefixes_warn:
        pn = p.rstrip("/")
        if rel == pn or rel.startswith(pn + "/"):
            return "WARN_ONLY"

    if rel.startswith("tests/") or "/tests/" in rel:
        return "WARN_ONLY"

    py_name = Path(rel).name
    if py_name.startswith("test_") or py_name.endswith("_test.py"):
        return "WARN_ONLY"

    return "CODE"


def _iter_files(root: Path, cfg: Dict[str, Any]) -> Iterable[Path]:
    exclude_names = set(cfg.get("exclude_dir_names", []) or ()) | _EXCLUDE_DIRS

    for path in root.rglob("*"):
        if path.is_dir():
            if path.name in exclude_names:
                continue
            continue
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(p in exclude_names for p in parts):
            continue
        suffix = path.suffix.lower()
        if suffix == "" and path.name in ("Dockerfile", "Makefile"):
            yield path
        elif suffix not in _SCAN_SUFFIXES:
            continue
        else:
            yield path


def _compile_rules(cfg: Dict[str, Any]) -> Tuple[List[Tuple[str, str, re.Pattern[str], str]], List[Tuple[str, str, re.Pattern[str], str]]]:
    from_json: List[Tuple[str, str, re.Pattern[str], str]] = []
    for row in cfg.get("patterns_fail_in_code", []) or []:
        rid = str(row["id"])
        label = str(row["label"])
        pat = re.compile(str(row["regex"]), re.MULTILINE)
        from_json.append((rid, label, pat, label))

    extra: List[Tuple[str, str, re.Pattern[str], str]] = []
    for row in _EXTRA_CODE_FAIL_RULES:
        extra.append((row["id"], row["label"], re.compile(row["regex"], re.MULTILINE), row["label"]))

    return from_json, extra


@dataclass
class PatternHit:
    pattern_id: str
    severity: Literal["WARN", "FAIL"]
    label: str
    path: str
    line_hint: Optional[str] = None


def _first_match_line(txt: str, start: int) -> Optional[str]:
    line_no = txt.count("\n", 0, start) + 1
    snippet = txt[start : start + 120].splitlines()[0].strip()
    return f"L{line_no}: {snippet[:200]}"


def scan_text_patterns(repo_root: Path, cfg: Dict[str, Any]) -> List[PatternHit]:
    json_rules, extra_rules = _compile_rules(cfg)
    hits: List[PatternHit] = []

    for path in _iter_files(repo_root, cfg):
        rel = path.relative_to(repo_root).as_posix()
        bucket = classify_path(rel, cfg)
        if bucket == "SKIP":
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for rule_id, label, pat, _ in json_rules + extra_rules:
            for m in pat.finditer(text):
                sev: Literal["WARN", "FAIL"] = "WARN" if bucket == "WARN_ONLY" else "FAIL"
                hits.append(
                    PatternHit(
                        pattern_id=rule_id,
                        severity=sev,
                        label=label,
                        path=rel,
                        line_hint=_first_match_line(text, m.start()),
                    )
                )

    return hits


def _pattern_code_fails(hits: Sequence[PatternHit]) -> List[PatternHit]:
    return [h for h in hits if h.severity == "FAIL"]


def _aggregate_status(
    pattern_hits: Sequence[PatternHit],
    policy_status: str,
    destructive_status: str,
) -> str:
    pattern_fails = _pattern_code_fails(pattern_hits)
    if pattern_fails or policy_status == "FAIL" or destructive_status == "FAIL":
        return "FAIL"

    pattern_warns = any(h.severity == "WARN" for h in pattern_hits)
    if (
        pattern_warns
        or policy_status == "PASS_WITH_WARNINGS"
        or destructive_status == "PASS_WITH_WARNINGS"
    ):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def _serialize_hits(hits: Sequence[PatternHit]) -> List[dict]:
    return [
        {
            "pattern_id": h.pattern_id,
            "severity": h.severity,
            "label": h.label,
            "path": h.path,
            "line": h.line_hint,
        }
        for h in hits
    ]


def run_safety_audit(
    repo_root: Optional[Path] = None,
    *,
    config_path: Optional[Path] = None,
    report_path: Optional[Path] = None,
) -> Tuple[str, Dict[str, Any]]:
    root = repo_root or REPO_ROOT
    cfg_path = config_path or (root / "tools" / "forbidden_audit_patterns.json")
    cfg = _load_json_config(cfg_path)

    pattern_hits = scan_text_patterns(root, cfg)
    pattern_code_fails = _pattern_code_fails(pattern_hits)

    policy_status, policy_rows = run_code_policy_audit(root)
    destructive_status, destructive_rows, destructive_meta = run_destructive_audit(root)
    if not destructive_meta.get("complete", False):
        destructive_status = "FAIL"

    final = _aggregate_status(pattern_hits, policy_status, destructive_status)

    report: Dict[str, Any] = {
        "status": final,
        "repo_root": str(root),
        "pattern_scan": {
            "hits": _serialize_hits(pattern_hits),
            "code_failures": len(pattern_code_fails),
        },
        "code_policy_audit": {
            "status": policy_status,
            "findings": serialize_policy(policy_rows),
        },
        "destructive_command_audit": {
            **destructive_meta,
            "status": destructive_status,
            "findings": serialize_destructive(destructive_rows),
        },
    }

    out_path = report_path or (root / "diagnostics" / "safety_audit_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return final, report


def _human_summary(status: str, report: Dict[str, Any]) -> str:
    lines = [
        f"Safety audit status: {status}",
        f"  Pattern code failures: {report['pattern_scan']['code_failures']}",
        f"  Pattern total hits: {len(report['pattern_scan']['hits'])}",
        f"  Code policy: {report['code_policy_audit']['status']} "
        f"({len(report['code_policy_audit']['findings'])} findings)",
        f"  Destructive commands: {report['destructive_command_audit']['status']} "
        f"({len(report['destructive_command_audit']['findings'])} call sites reviewed)",
        f"  destructive_command_audit.complete={report['destructive_command_audit'].get('complete')}",
    ]
    if status == "FAIL":
        lines.append("")
        lines.append("FAIL 블록 (발견 시 실제 destructive --apply 불가)")
        for h in report["pattern_scan"]["hits"]:
            if h["severity"] == "FAIL":
                lines.append(f"  - [{h['pattern_id']}] {h['path']}: {h['label']}")
        for row in report["code_policy_audit"]["findings"]:
            if row["severity"] == "FAIL":
                lines.append(f"  - policy {row['check_id']} {row['path']}: {row['message']}")
        if report["destructive_command_audit"]["status"] == "FAIL":
            for row in report["destructive_command_audit"]["findings"]:
                if row["category"] == "DESTRUCTIVE_UNGUARDED":
                    lines.append(
                        "  - DESTRUCTIVE_UNGUARDED "
                        f"{row['path']}:{row['lineno']} ({row['detail']})"
                    )
    elif status == "PASS_WITH_WARNINGS":
        warns = sum(1 for h in report["pattern_scan"]["hits"] if h["severity"] == "WARN")
        if warns:
            lines.append(f"(경고 패턴 매치 수: 문서·테스트 제외 규칙으로 WARN 처리됨 × {warns})")
        lines.append("PASS_WITH_WARNINGS — 실패는 없으며 경고 위주 검토 가능")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Recoverix destructive-op static safety audit")
    parser.add_argument("--json", action="store_true", help="표준 출력에 최종 리포트 JSON 출력")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="forbidden_audit_patterns.json 위치 재정의",
    )
    parser.add_argument("--report-path", type=Path, default=None, help="JSON 리포트 저장 경로")
    args = parser.parse_args(argv)

    status, report = run_safety_audit(config_path=args.config, report_path=args.report_path)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    print(_human_summary(status, report))
    return 0 if status in ("PASS", "PASS_WITH_WARNINGS") else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Structural policy checks for restore/backup/agent/validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]


def _root(repo_root: Path | None) -> Path:
    return repo_root if repo_root is not None else REPO_ROOT


@dataclass(frozen=True)
class PolicyFinding:
    check_id: str
    severity: str
    message: str
    path: str


def read_text(rel: str, *, repo_root: Path | None = None) -> str:
    return (_root(repo_root) / rel).read_text(encoding="utf-8")


def check_run_restore(*, repo_root: Path | None = None) -> List[PolicyFinding]:
    rel = "restore_engine/run_restore.py"
    txt = read_text(rel, repo_root=repo_root)
    out: List[PolicyFinding] = []
    if "authorize_restore_execution" not in txt:
        out.append(PolicyFinding("RS1", "FAIL", "authorize_restore_execution 없음", rel))
    if "RestoreExecutor" not in txt:
        out.append(PolicyFinding("RS2", "FAIL", "RestoreExecutor 없음", rel))
    auth = txt.find("authorize_restore_execution(")
    ex = txt.find("RestoreExecutor(")
    if auth >= 0 and ex >= 0 and auth > ex:
        out.append(PolicyFinding("RS3", "FAIL", "authorization 이 executor 생성 이후", rel))
    for token in ("--apply", "--confirm", "--phrase"):
        if token not in txt:
            out.append(PolicyFinding("RS4", "FAIL", f"필수 CLI 토큰 누락: {token}", rel))
    return out


def check_restore_executor(*, repo_root: Path | None = None) -> List[PolicyFinding]:
    rel = "restore_engine/restore_executor.py"
    txt = read_text(rel, repo_root=repo_root)
    out: List[PolicyFinding] = []
    if "_assert_authorized" not in txt:
        out.append(PolicyFinding("RE1", "FAIL", "_assert_authorized 없음", rel))
    if "validate_restore(" not in txt:
        out.append(PolicyFinding("RE2", "FAIL", "validate_restore 호출 없음", rel))
    ex = txt.find("def execute(")
    if ex < 0:
        out.append(PolicyFinding("RE3a", "FAIL", "RestoreExecutor.execute 없음", rel))
        return out
    chunk = txt[ex : ex + 4500]
    idx_val = chunk.find("validation = validate_restore")
    idx_backup_efi = chunk.find("self._stage_backup_efi()")
    idx_partclone = chunk.find("self._stage_partclone_windows()")
    if idx_val < 0:
        out.append(PolicyFinding("RE3b", "FAIL", "execute 본문에 validate_restore 없음", rel))
    else:
        if idx_backup_efi >= 0 and idx_val > idx_backup_efi:
            out.append(PolicyFinding("RE3", "FAIL", "validate_restore 가 EFI 백업 단계 이후", rel))
        if idx_partclone >= 0 and idx_val > idx_partclone:
            out.append(PolicyFinding("RE3c", "FAIL", "validate_restore 가 partclone 이후", rel))
    if "_assert_target_allowed" not in txt:
        out.append(PolicyFinding("RE4", "WARN", "recovery 파티션 보호 _assert_target_allowed 없음", rel))
    if "while True" in txt:
        block = txt[txt.find("while True") : txt.find("while True") + 400]
        if "restore" in block.lower() and "retry" in block.lower():
            out.append(PolicyFinding("RE5", "FAIL", "restore 자동 재시도 루프 의심", rel))
    return out


def check_run_backup(*, repo_root: Path | None = None) -> List[PolicyFinding]:
    rel = "backup_engine/run_backup.py"
    txt = read_text(rel, repo_root=repo_root)
    out: List[PolicyFinding] = []
    for token in ("--apply", "--confirm"):
        if token not in txt:
            out.append(PolicyFinding("BK1", "FAIL", f"필수 CLI 토큰 누락: {token}", rel))
    if "finalize_backup_manifest" not in txt:
        out.append(PolicyFinding("BK2", "FAIL", "finalize_backup_manifest 없음", rel))
    fn = txt.find("def execute_backup_writes")
    if fn >= 0:
        window = txt[fn : fn + 4000]
        if "finalize_backup_manifest" not in window:
            out.append(PolicyFinding("BK3", "FAIL", "finalize_backup_manifest 가 쓰기 경로에 없음", rel))
    if "read_bitlocker_state" not in txt:
        out.append(PolicyFinding("BK4", "WARN", "BitLocker 검사 심블 read_bitlocker_state 검토 필요", rel))
    return out


def check_image_validation_fail_closed(*, repo_root: Path | None = None) -> List[PolicyFinding]:
    rel = "validation/image_validation.py"
    txt = read_text(rel, repo_root=repo_root)
    out: List[PolicyFinding] = []
    if "has_incomplete_backup" not in txt and "incomplete" not in txt:
        out.append(PolicyFinding("VL1", "FAIL", "incomplete 백업 관련 문자열 신호 적음", rel))
    ix = txt.find("except Exception")
    if ix < 0 or "allowed=False" not in txt[ix : ix + 500]:
        out.append(PolicyFinding("VL2", "WARN", "validate_restore 예외 시 FAIL CLOSED 패턴 명시 블달", rel))
    return out


def _strip_approx_docstrings(src: str) -> str:
    stripped = re.sub(r'"""[\s\S]*?"""', "", src)
    stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
    return stripped


def windows_agent_checks(*, repo_root: Path | None = None) -> List[PolicyFinding]:
    out: List[PolicyFinding] = []
    root = _root(repo_root)
    base = root / "windows_agent"

    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts or path.name.startswith("test"):
            continue
        rel = path.relative_to(root).as_posix()
        code = path.read_text(encoding="utf-8")
        naked = _strip_approx_docstrings(code)

        lowered = "\n".join(ln for ln in naked.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))

        bad_tokens = (
            "partclone.",
            "partclone ",
            "squashfs",
            "ext4",
            "recovery_state.json",
            "save_recovery_state",
            "load_recovery_state",
            "mount -t ext4",
        )

        lc = lowered.lower()

        for tok in bad_tokens:
            if tok in lc:
                out.append(PolicyFinding("WA1", "FAIL", f"windows_agent 에 금지 토큰 포함: {tok}", rel))
                break

    return out


def run_code_policy_audit(repo_root: Path | None = None) -> Tuple[str, List[PolicyFinding]]:
    rows: List[PolicyFinding] = []
    rows.extend(check_run_restore(repo_root=repo_root))
    rows.extend(check_restore_executor(repo_root=repo_root))
    rows.extend(check_run_backup(repo_root=repo_root))
    rows.extend(check_image_validation_fail_closed(repo_root=repo_root))
    rows.extend(windows_agent_checks(repo_root=repo_root))

    fails = [r for r in rows if r.severity == "FAIL"]
    if fails:
        return "FAIL", rows
    warns = [r for r in rows if r.severity == "WARN"]
    return ("PASS_WITH_WARNINGS" if warns else "PASS"), rows


def serialize(rows: Sequence[PolicyFinding]) -> List[dict]:
    return [
        {
            "check_id": r.check_id,
            "severity": r.severity,
            "message": r.message,
            "path": r.path,
        }
        for r in rows
    ]


if __name__ == "__main__":
    st, rows = run_code_policy_audit()
    print(st, len(rows))

#!/usr/bin/env python3
"""
파괴적 backup/restore 테스트 직전 자동 게이트.

- safety_audit
- integration checks (--dry-run 필수)
- destructive_command_audit 완전성(complete)
- (Linux + RECOVERY_IMAGE 마운트 시) BitLocker, validate_restore, rollback 플래그,
  incomplete_backup, Windows Boot Manager 검증, RecoveryBoot shim 존재

FAIL 시 exit code 1. 리포트는 diagnostics/pre_destructive_gate.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = REPO_ROOT / "diagnostics" / "pre_destructive_gate.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

RECOVERY_SHIM = Path("EFI/RecoveryBoot/shimx64.efi")


@dataclass
class GateStep:
    id: str
    status: str  # PASS | FAIL | WARN | SKIPPED
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_safety() -> GateStep:
    from tools.safety_audit import run_safety_audit

    st, rep = run_safety_audit()
    ok = st in ("PASS", "PASS_WITH_WARNINGS")
    return GateStep(
        id="safety_audit",
        status="PASS" if ok else "FAIL",
        detail=st,
        data={"report_status": st},
    )


def _run_destructive_complete() -> GateStep:
    from tools.destructive_command_audit import run_destructive_audit

    st, _rows, meta = run_destructive_audit()
    complete = bool(meta.get("complete"))
    # 생산 경로 DESTRUCTIVE_UNGUARDED → destructive audit status FAIL
    ok = complete and st != "FAIL"
    return GateStep(
        id="destructive_command_audit",
        status="PASS" if ok else "FAIL",
        detail=f"status={st}, complete={complete}",
        data=dict(meta),
    )


def _run_integration() -> GateStep:
    script = REPO_ROOT / "tools" / "run_integration_checks.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    report_path = REPO_ROOT / "diagnostics" / "integration_report.json"
    payload: Dict[str, Any] = {}
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return GateStep(
                id="integration_checks",
                status="FAIL",
                detail=f"invalid integration_report.json: {exc}",
                data={"returncode": proc.returncode, "stderr": proc.stderr[:2000]},
            )

    status = payload.get("status", "UNKNOWN")
    # run_integration_checks: hardcoding FAIL 만 전체 FAIL; 그 외 경고는 PASS_WITH_WARNINGS
    ok = status in ("PASS", "PASS_WITH_WARNINGS")
    step = GateStep(
        id="integration_checks",
        status="PASS" if ok else "FAIL",
        detail=status,
        data={"returncode": proc.returncode, "integration_status": status},
    )
    if proc.returncode != 0 and ok:
        step.data["note"] = "non-zero returncode but acceptable status (see integration script)"
    return step


def _linux_runtime_checks() -> List[GateStep]:
    steps: List[GateStep] = []
    if sys.platform != "linux":
        steps.append(
            GateStep(
                id="linux_runtime_bundle",
                status="SKIPPED",
                detail=f"not Linux ({sys.platform}); run from Recovery Runtime for full checks",
            )
        )
        return steps

    from backup_engine.backup_planner import discover_layout, read_bitlocker_state
    from backup_engine.backup_state import has_incomplete_backup
    from backup_engine.run_backup import build_disk_metadata
    from recovery_runtime.discover import discover_recovery_volumes
    from recovery_runtime.mounts import resolve_mount_path
    from restore_engine.restore_planner import _find_efi_mount, verify_windows_boot_manager
    from restore_engine.restore_state import load_recovery_state
    from validation.image_validation import validate_restore

    bl = read_bitlocker_state(live=True)
    if bl.strip().upper() == "ON":
        steps.append(GateStep(id="bitlocker", status="FAIL", detail="BitLocker ON"))
    elif bl.strip().upper() == "UNKNOWN":
        steps.append(
            GateStep(
                id="bitlocker",
                status="WARN",
                detail="BitLocker UNKNOWN — confirm manually before destructive test",
                data={"raw": bl},
            )
        )
    else:
        steps.append(GateStep(id="bitlocker", status="PASS", detail=bl))

    topo, layout = discover_layout()
    if topo or layout is None:
        steps.append(
            GateStep(
                id="discover_layout",
                status="FAIL",
                detail=topo or "layout is None",
            )
        )
        return steps
    steps.append(GateStep(id="discover_layout", status="PASS", detail="layout ok"))

    image, _linux, _vols = discover_recovery_volumes()
    if image is None:
        steps.append(
            GateStep(
                id="recovery_root",
                status="FAIL",
                detail="RECOVERY_IMAGE volume not found — cannot validate restore",
            )
        )
        return steps

    recovery_root = resolve_mount_path(image.mountpoint, "RECOVERY_IMAGE")
    if recovery_root is None or not recovery_root.exists():
        steps.append(
            GateStep(
                id="recovery_root",
                status="FAIL",
                detail="recovery root not mounted — mount RECOVERY_IMAGE first",
            )
        )
        return steps

    steps.append(
        GateStep(
            id="recovery_root",
            status="PASS",
            detail=str(recovery_root),
            data={"path": str(recovery_root)},
        )
    )

    incomplete = has_incomplete_backup(recovery_root)
    steps.append(
        GateStep(
            id="incomplete_backup",
            status="FAIL" if incomplete else "PASS",
            detail="incomplete marker present" if incomplete else "no incomplete marker",
        )
    )

    st = load_recovery_state(recovery_root)
    rb = st.rollback_required
    steps.append(
        GateStep(
            id="rollback_required",
            status="FAIL" if rb else "PASS",
            detail=f"rollback_required={rb}",
        )
    )

    disk = build_disk_metadata(layout)
    vr = validate_restore(recovery_root, disk)
    vok = vr.allowed
    steps.append(
        GateStep(
            id="validate_restore",
            status="PASS" if vok else "FAIL",
            detail=f"{vr.status}: {vr.reason or ''}",
            data=vr.to_dict(),
        )
    )

    wm = verify_windows_boot_manager(layout)
    wm_ok = wm.status == "PASS"
    steps.append(
        GateStep(
            id="windows_boot_manager",
            status="PASS" if wm_ok else "FAIL",
            detail=f"{wm.status}: {wm.reason or ''}",
            data=dict(wm.details) if getattr(wm, "details", None) else {},
        )
    )

    efi_mount = _find_efi_mount(layout.efi.path)
    if efi_mount is None:
        steps.append(
            GateStep(
                id="recoveryboot_esp",
                status="FAIL",
                detail="ESP not mounted read-only — cannot verify RecoveryBoot shim",
            )
        )
    else:
        shim = efi_mount / RECOVERY_SHIM
        shim_ok = shim.is_file()
        steps.append(
            GateStep(
                id="recoveryboot_esp",
                status="PASS" if shim_ok else "FAIL",
                detail=str(shim),
                data={"exists": shim_ok},
            )
        )

    return steps


def run_gate() -> tuple[str, Dict[str, Any]]:
    steps: List[GateStep] = []
    steps.append(_run_safety())
    steps.append(_run_integration())
    steps.append(_run_destructive_complete())
    steps.extend(_linux_runtime_checks())

    def _sev(s: GateStep) -> int:
        if s.status == "FAIL":
            return 2
        if s.status == "WARN":
            return 1
        return 0

    worst = max((_sev(s) for s in steps), default=0)
    if worst >= 2:
        overall = "FAIL"
    elif worst == 1:
        overall = "PASS_WITH_WARNINGS"
    else:
        overall = "PASS"

    report: Dict[str, Any] = {
        "overall": overall,
        "generated_at": _utc(),
        "repo_root": str(REPO_ROOT),
        "message": (
            "destructive test allowed (review WARNs)"
            if overall == "PASS_WITH_WARNINGS"
            else ("destructive test BLOCKED" if overall == "FAIL" else "destructive test allowed")
        ),
        "steps": [asdict(s) for s in steps],
    }
    return overall, report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-destructive automated gate")
    parser.add_argument("--json", action="store_true", help="Print report JSON to stdout")
    args = parser.parse_args(argv)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    overall, report = run_gate()
    OUT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"pre_destructive_gate: {overall}")
    print(f"Wrote {OUT_PATH}")

    if overall == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

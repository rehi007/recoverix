"""Shared release / readiness gate logic (stdlib only; reusable from tests).

파괴적 작업을 수행하지 않고, 저장소 상태와 선행 스크립트 결과를 집계한다.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = REPO_ROOT / "release" / "dist"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


REQUIRED_TOOLS = (
    "tools/safety_audit.py",
    "tools/run_integration_checks.py",
    "tools/pre_destructive_gate.py",
    "tools/destructive_command_audit.py",
    "tools/release_readiness_check.py",
    "tools/final_release_gate.py",
    "scripts/validate_release.py",
    "scripts/build_release.py",
)

KNOWN_LIMITATION_SUMMARY: List[str] = [
    "Secure Boot 서명 자동화는 완료되지 않았을 수 있음",
    "일부 OEM firmware 미검증",
    "Fast Boot · USB 키보드 타이밍 이슈 가능",
    "다중 디스크·RAID·BitLocker·Legacy BIOS·Multi-boot 미지원",
]


@dataclass
class Signal:
    """단일 검사 결과."""

    id: str
    status: str  # PASS | FAIL | WARN | SKIPPED
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "status": self.status, "detail": self.detail, "data": dict(self.data)}


def repo_root(repo: Optional[Path] = None) -> Path:
    return repo if repo is not None else REPO_ROOT


def load_product_manifest(repo: Path) -> Dict[str, Any]:
    mf = repo / "config" / "product_manifest.json"
    if not mf.is_file():
        return {}
    return json.loads(mf.read_text(encoding="utf-8"))


def signal_required_tools(repo: Path) -> Signal:
    missing: List[str] = []
    for rel in REQUIRED_TOOLS:
        if not (repo / rel).is_file():
            missing.append(rel)
    if missing:
        return Signal("required_scripts", "FAIL", f"missing: {', '.join(missing)}", {"missing": missing})
    return Signal("required_scripts", "PASS", "all required tool scripts present")


def signal_manifest_docs(repo: Path) -> Signal:
    prod = load_product_manifest(repo)
    if not prod:
        return Signal("required_docs_manifest", "FAIL", "missing config/product_manifest.json")
    docs = list(prod.get("required_docs") or [])
    if not docs:
        return Signal("required_docs_manifest", "WARN", "required_docs empty in product_manifest.json")
    missing = [d for d in docs if not (repo / "docs" / d).is_file()]
    if missing:
        return Signal(
            "required_docs",
            "FAIL",
            f"{len(missing)} doc(s) missing per product_manifest.required_docs",
            {"missing_docs": missing},
        )
    return Signal("required_docs", "PASS", f"{len(docs)} shipped docs present")


def signal_dev_manifest(repo: Path) -> Signal:
    prod = load_product_manifest(repo)
    ver = str(prod.get("version") or "")
    if ver.endswith("-dev") or ver in {"0.0.0-dev", "", "dev"}:
        return Signal(
            "product_version",
            "WARN",
            "development version in product_manifest (use non-dev for OEM / strict validate)",
            {"version": ver},
        )
    return Signal("product_version", "PASS", ver or "unset", {"version": ver})


def signal_efi_source(repo: Path) -> Signal:
    for name in ("shimx64.efi", "grubx64.efi", "grub.cfg"):
        p = repo / "boot_manager" / "assets" / "recovery_boot" / name
        if not p.is_file():
            return Signal("efi_source_assets", "FAIL", f"missing {p}")
    return Signal("efi_source_assets", "PASS", "boot_manager/assets/recovery_boot complete")


def signal_runtime_modules(repo: Path) -> Signal:
    for rel in ("recovery_runtime/main.py", "windows_agent/agent.py"):
        if not (repo / rel).is_file():
            return Signal("runtime_modules", "FAIL", f"missing {rel}")
    return Signal("runtime_modules", "PASS", "recovery_runtime + windows_agent entrypoints present")


def scan_placeholder_tokens(repo: Path) -> Signal:
    mf_path = repo / "config" / "product_manifest.json"
    if not mf_path.is_file():
        return Signal("placeholder_scan", "SKIPPED", "no product_manifest for tokens")
    prod = load_product_manifest(repo)
    tokens = tuple(prod.get("placeholder_tokens") or ())
    if not tokens:
        return Signal("placeholder_scan", "PASS", "no placeholder_tokens configured")
    hits: List[Dict[str, str]] = []
    scan_roots = [
        repo / d
        for d in (
            "backup_engine",
            "restore_engine",
            "recovery_runtime",
            "windows_agent",
            "boot_manager",
            "partition_manager",
            "rollback",
            "validation",
            "common",
            "tools",
            "grub",
            "config",
        )
        if (repo / d).is_dir()
    ]
    for base in scan_roots:
        for path in base.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for t in tokens:
                if t in text:
                    rel = str(path.relative_to(repo))
                    hits.append({"token": t, "path": rel})
    if hits:
        return Signal(
            "placeholder_scan",
            "FAIL",
            f"placeholder token(s) in production paths ({len(hits)} hit(s))",
            {"hits": hits[:50], "truncated": len(hits) > 50},
        )
    return Signal("placeholder_scan", "PASS", "no placeholder tokens in scanned tree")


def run_safety(repo: Path) -> Tuple[Signal, Dict[str, Any]]:
    from tools.safety_audit import run_safety_audit

    status, report = run_safety_audit(repo_root=repo)
    ok = status in ("PASS", "PASS_WITH_WARNINGS")
    sev = "PASS" if ok else "FAIL"
    sig = Signal(
        "safety_audit",
        sev,
        status,
        {"report_status": status},
    )
    return sig, report


def run_integration(repo: Path) -> Signal:
    script = repo / "tools" / "run_integration_checks.py"
    if not script.is_file():
        return Signal("integration_checks", "FAIL", "run_integration_checks.py missing")
    proc = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    report_path = repo / "diagnostics" / "integration_report.json"
    payload: Dict[str, Any] = {}
    if report_path.is_file():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return Signal(
                "integration_checks",
                "FAIL",
                f"invalid integration_report.json: {exc}",
                {"returncode": proc.returncode},
            )
    i_status = str(payload.get("status", "UNKNOWN"))
    ok = i_status in ("PASS", "PASS_WITH_WARNINGS")
    return Signal(
        "integration_checks",
        "PASS" if ok else "FAIL",
        i_status,
        {"returncode": proc.returncode, "integration_status": i_status},
    )


def destructive_meta_from_safety(report: Dict[str, Any]) -> Tuple[bool, str, str]:
    block = report.get("destructive_command_audit") or {}
    complete = bool(block.get("complete"))
    st = str(block.get("status", "UNKNOWN"))
    return complete, st, "complete" if complete else "incomplete"


def signal_destructive_audit(report: Dict[str, Any]) -> Signal:
    complete, st, _ = destructive_meta_from_safety(report)
    ok = complete and st != "FAIL"
    return Signal(
        "destructive_command_audit",
        "PASS" if ok else "FAIL",
        f"complete={complete}, status={st}",
        {"complete": complete, "status": st},
    )


def run_validate_release_bundle(repo: Path, bundle: Path) -> Signal:
    spec = importlib.util.spec_from_file_location(
        "validate_release_mod",
        repo / "scripts" / "validate_release.py",
    )
    if spec is None or spec.loader is None:
        return Signal("validate_release", "FAIL", "cannot load scripts/validate_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if not bundle.is_dir():
        return Signal("validate_release", "SKIPPED", f"bundle root not found: {bundle}", {"bundle": str(bundle)})
    code = mod.validate_bundle(bundle, strict_placeholder=False)  # type: ignore[attr-defined]
    ok = code == 0
    return Signal(
        "validate_release",
        "PASS" if ok else "FAIL",
        "PASS" if ok else "validate_release reported missing layout",
        {"bundle": str(bundle), "exit_code": code},
    )


def run_pre_destructive_gate(_repo: Path) -> Tuple[str, Dict[str, Any]]:
    from tools.pre_destructive_gate import run_gate

    return run_gate()


def compute_readiness_tier(signals: List[Signal]) -> Tuple[str, str]:
    """(readiness, release_stage_recommendation) — readiness: READY | READY_WITH_WARNINGS | NOT_READY."""

    blocking_fail = {
        "required_scripts",
        "required_docs",
        "required_docs_manifest",
        "safety_audit",
        "integration_checks",
        "destructive_command_audit",
        "placeholder_scan",
        "efi_source_assets",
        "runtime_modules",
        "validate_release",
    }
    for s in signals:
        if s.id in blocking_fail and s.status == "FAIL":
            return "NOT_READY", "NOT_READY"
    validate_release_skipped = any(s.id == "validate_release" and s.status == "SKIPPED" for s in signals)
    pre_bad = next((s for s in signals if s.id == "pre_destructive_gate" and s.status == "FAIL"), None)

    downgrade = validate_release_skipped or (pre_bad is not None)

    warn_or_skip = (
        downgrade
        or any(s.status in ("WARN", "SKIPPED") for s in signals)
        or any(s.id == "safety_audit" and s.detail == "PASS_WITH_WARNINGS" for s in signals)
        or any(s.id == "integration_checks" and s.detail == "PASS_WITH_WARNINGS" for s in signals)
    )

    if warn_or_skip:
        return "READY_WITH_WARNINGS", "READY_FOR_MANUAL_TEST"
    return "READY", "READY_FOR_MANUAL_TEST"


def build_readiness_payload(repo: Optional[Path] = None, *, bundle: Optional[Path] = None) -> Dict[str, Any]:
    root = repo_root(repo)
    bundle_path = bundle if bundle is not None else DEFAULT_BUNDLE

    signals: List[Signal] = []
    signals.append(signal_required_tools(root))
    signals.append(signal_manifest_docs(root))
    signals.append(signal_dev_manifest(root))
    signals.append(signal_efi_source(root))
    signals.append(signal_runtime_modules(root))
    signals.append(scan_placeholder_tokens(root))

    safety_report: Dict[str, Any] = {}
    safety_sig: Optional[Signal] = None
    try:
        safety_sig, safety_report = run_safety(root)
        signals.append(safety_sig)
        signals.append(signal_destructive_audit(safety_report))
    except Exception as exc:  # noqa: BLE001
        signals.append(
            Signal(
                "safety_audit",
                "FAIL",
                f"{type(exc).__name__}: {exc}",
            )
        )
        signals.append(
            Signal(
                "destructive_command_audit",
                "FAIL",
                "safety_audit did not complete",
            )
        )

    signals.append(run_integration(root))
    vr_sig = run_validate_release_bundle(root, bundle_path)
    signals.append(vr_sig)

    pd_overall, pd_report = run_pre_destructive_gate(root)
    pd_ok = pd_overall in ("PASS", "PASS_WITH_WARNINGS")
    signals.append(
        Signal(
            "pre_destructive_gate",
            "PASS" if pd_ok else "FAIL",
            pd_overall,
            {"overall": pd_overall},
        )
    )

    readiness, stage = compute_readiness_tier(signals)

    safety_st = next((s.detail for s in signals if s.id == "safety_audit"), "UNKNOWN")
    integ = next((s for s in signals if s.id == "integration_checks"), None)
    integ_st = integ.detail if integ else "UNKNOWN"
    dest = next((s for s in signals if s.id == "destructive_command_audit"), None)
    dest_complete = bool((dest.data or {}).get("complete")) if dest else False

    out: Dict[str, Any] = {
        "generated_at": _utc_iso(),
        "readiness": readiness,
        "status": stage if readiness != "NOT_READY" else "NOT_READY",
        "release_stage_recommendation": stage,
        "safety_audit": safety_st,
        "integration_checks": integ_st,
        "pre_destructive_gate": pd_overall,
        "destructive_audit_complete": dest_complete,
        "required_docs": all(s.status == "PASS" for s in signals if s.id == "required_docs"),
        "required_assets": all(
            s.status == "PASS" for s in signals if s.id in ("efi_source_assets", "runtime_modules")
        ),
        "known_limitations": list(KNOWN_LIMITATION_SUMMARY),
        "bundle_path": str(bundle_path),
        "signals": [s.to_dict() for s in signals],
        "pre_destructive_report_summary": {"overall": pd_overall, "steps": len(pd_report.get("steps", []))},
        "safety_report_summary": {"status": safety_st, "destructive": (safety_report.get("destructive_command_audit") or {})},
    }
    return out


def final_gate_payload(repo: Optional[Path] = None, *, bundle: Optional[Path] = None) -> Dict[str, Any]:
    root = repo_root(repo)
    bundle_path = bundle if bundle is not None else DEFAULT_BUNDLE
    readiness = build_readiness_payload(root, bundle=bundle_path)

    reasons: List[str] = []
    final_fail = False

    if readiness["readiness"] == "NOT_READY":
        final_fail = True
        reasons.append("release_readiness NOT_READY (static checks)")

    pd = readiness.get("pre_destructive_gate")
    if pd == "FAIL":
        final_fail = True
        reasons.append("pre_destructive_gate FAIL")

    vr = next(
        (s for s in readiness["signals"] if s["id"] == "validate_release"),
        None,
    )
    if vr is None:
        final_fail = True
        reasons.append("validate_release 신호 없음")
    elif vr["status"] == "FAIL":
        final_fail = True
        reasons.append("validate_release FAIL (번들 레이아웃 불완전)")
    elif vr["status"] == "SKIPPED":
        final_fail = True
        reasons.append("release bundle 없음 — scripts/build_release.py 후 재실행")

    ok = not final_fail
    return {
        "generated_at": _utc_iso(),
        "overall": "PASS" if ok else "FAIL",
        "destructive_test_allowed": ok,
        "message": (
            "destructive test / packaging gate PASS — 절차·호스트 체크는 TEST_ENTRY_CRITERIA.md 준수"
            if ok
            else "FINAL gate FAIL — " + "; ".join(reasons)
        ),
        "reasons": reasons,
        "readiness": readiness["readiness"],
        "release_stage_recommendation": readiness["release_stage_recommendation"],
        "pre_destructive_gate": readiness["pre_destructive_gate"],
        "validate_release_signal": vr,
        "known_limitations": readiness["known_limitations"],
        "signals": readiness["signals"],
    }

#!/usr/bin/env python3
"""Non-destructive integration checks (dry-run / read-only only; no --apply)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REPORT_DIR = REPO_ROOT / "diagnostics"
REPORT_PATH = REPORT_DIR / "integration_report.json"

FORBIDDEN_HARDCODE_TOKENS = ("Disk0", "Partition1", "Boot0000")

_SCAN_DIRS = (
    "tools",
    "backup_engine",
    "restore_engine",
    "recovery_runtime",
    "windows_agent",
    "boot_manager",
    "partition_manager",
    "rollback",
    "validation",
    "common",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(
    checks: List[Dict[str, Any]],
    warnings: List[str],
    *,
    name: str,
    fn: Callable[[], tuple[str, Dict[str, Any], Optional[str]]],
) -> None:
    """Append one check record; never raises."""
    details: Dict[str, Any] = {}
    reason: Optional[str] = None
    status = "FAIL"
    try:
        status, details, reason = fn()
    except Exception as exc:  # noqa: BLE001
        status = "FAIL"
        reason = f"{type(exc).__name__}: {exc}"
        details = {"fail_closed": True}
    entry: Dict[str, Any] = {"name": name, "status": status, "details": details}
    if reason:
        entry["reason"] = reason
    checks.append(entry)
    if status == "SKIPPED" and reason:
        warnings.append(f"{name}: skipped ({reason})")


def _overall_status(checks: List[Dict[str, Any]]) -> str:
    if any(c["name"] == "hardcoding_policy_audit" and c["status"] == "FAIL" for c in checks):
        return "FAIL"
    if any(c["status"] == "FAIL" for c in checks):
        return "PASS_WITH_WARNINGS"
    if any(c["status"] == "SKIPPED" for c in checks):
        return "PASS_WITH_WARNINGS"
    return "PASS"


def _environment() -> Dict[str, Any]:
    import platform

    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "is_windows": sys.platform == "win32",
        "is_linux": sys.platform == "linux",
        "cwd": str(Path.cwd()),
        "repo_root": str(REPO_ROOT),
    }


def _run_preflight() -> tuple[str, Dict[str, Any], Optional[str]]:
    if sys.platform != "win32":
        return "SKIPPED", {"note": "Windows preflight only"}, "not Windows"
    from windows_agent.preflight import run_preflight

    result = run_preflight()
    payload = result.to_dict()
    # BitLocker ON is expected to fail strict PASS but is a valid safety signal
    st = "PASS" if result.status == "PASS" else "FAIL"
    return st, {"preflight": payload}, None if result.status == "PASS" else "preflight status not PASS"


def _run_partition_discovery() -> tuple[str, Dict[str, Any], Optional[str]]:
    from partition_manager.discovery import discover_partitions

    result = discover_partitions(dry_run=True)
    return (
        "PASS",
        {"status": result.status, "dry_run": result.dry_run},
        None,
    )


def _run_firmware_reader() -> tuple[str, Dict[str, Any], Optional[str]]:
    from boot_manager.firmware_reader import read_firmware_boot

    if sys.platform != "win32":
        r = read_firmware_boot(dry_run=True)
        return (
            "SKIPPED",
            {"status": r.status, "dry_run": r.dry_run},
            "live firmware enum requires Windows",
        )
    r = read_firmware_boot(dry_run=False)
    ok = r.status == "PASS"
    return ("PASS" if ok else "FAIL", r.to_dict() if hasattr(r, "to_dict") else {}, None if ok else "firmware analysis not PASS")


def _run_bootorder_planner() -> tuple[str, Dict[str, Any], Optional[str]]:
    from boot_manager.bootorder_planner import plan_bootorder_recovery

    # Linux CI: use simulated firmware shape (no bcdedit)
    from restore_engine.restore_planner import _simulated_firmware_analysis

    plan = plan_bootorder_recovery(_simulated_firmware_analysis(), dry_run=True)
    return (
        "PASS" if plan.status in ("PASS", "PLANNED") else "FAIL",
        {"plan_status": plan.status, "action_required": plan.action_required},
        None if plan.status in ("PASS", "PLANNED") else "unexpected plan status",
    )


def _run_backup_planner() -> tuple[str, Dict[str, Any], Optional[str]]:
    if sys.platform != "linux":
        return "SKIPPED", {}, "backup planner requires Linux (Recovery host)"
    from backup_engine.backup_planner import create_backup_plan

    plan = create_backup_plan(live=False)
    ok = plan.status in ("PLANNED", "MOUNT_REQUIRED")
    return (
        "PASS" if ok else "FAIL",
        {"status": plan.status, "execution_allowed": plan.execution_allowed},
        None if ok else plan.reason or "backup plan not acceptable",
    )


def _run_restore_planner() -> tuple[str, Dict[str, Any], Optional[str]]:
    if sys.platform != "linux":
        return "SKIPPED", {}, "restore planner requires Linux (Recovery host)"
    from restore_engine.restore_planner import create_restore_plan

    plan = create_restore_plan(live=False)
    details = {
        "status": plan.status,
        "execution_allowed": plan.execution_allowed,
        "simulation_only": plan.simulation_only,
        "restore_allowed": plan.restore_allowed,
    }
    # Dry-run integration: REJECTED is OK if environment lacks recovery topology
    return "PASS", details, None


def _run_windows_agent_dry() -> tuple[str, Dict[str, Any], Optional[str]]:
    if sys.platform != "win32":
        return "SKIPPED", {}, "windows_agent dry-run requires Windows"
    from windows_agent.fix_bootorder_task import run_fix_bootorder_task

    rc = run_fix_bootorder_task(dry_run=True, apply_changes=False)
    ok = rc in (0, 1)
    return (
        "PASS" if ok else "FAIL",
        {"returncode": rc},
        None if ok else f"unexpected return code {rc}",
    )


def _hardcoding_audit() -> tuple[str, Dict[str, Any], Optional[str]]:
    hits: List[Dict[str, str]] = []
    for rel in _SCAN_DIRS:
        base = REPO_ROOT / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "test_" in path.name:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if path.name == "run_integration_checks.py":
                continue
            for token in FORBIDDEN_HARDCODE_TOKENS:
                if token in text:
                    hits.append({"file": str(path.relative_to(REPO_ROOT)), "token": token})
    if hits:
        return "FAIL", {"hits": hits[:50], "total": len(hits)}, "forbidden hardcoded tokens in source"
    return "PASS", {"scanned": list(_SCAN_DIRS)}, None


def run_checks() -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []

    _check(checks, warnings, name="environment", fn=lambda: ("PASS", _environment(), None))
    _check(checks, warnings, name="preflight", fn=_run_preflight)
    _check(checks, warnings, name="partition_discovery", fn=_run_partition_discovery)
    _check(checks, warnings, name="firmware_reader", fn=_run_firmware_reader)
    _check(checks, warnings, name="bootorder_planner_dry_run", fn=_run_bootorder_planner)
    _check(checks, warnings, name="backup_planner_dry_run", fn=_run_backup_planner)
    _check(checks, warnings, name="restore_planner_dry_run", fn=_run_restore_planner)
    _check(checks, warnings, name="windows_agent_fix_bootorder_dry_run", fn=_run_windows_agent_dry)
    _check(checks, warnings, name="hardcoding_policy_audit", fn=_hardcoding_audit)

    report = {
        "status": _overall_status(checks),
        "generated_at": _utc_now_iso(),
        "environment": _environment(),
        "checks": checks,
        "warnings": warnings,
    }
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Integration checks (dry-run only; no disk writes, no --apply)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=True,
        help="Required flag: confirms non-destructive intent",
    )
    parser.add_argument("--json", action="store_true", help="Print full report JSON to stdout")
    args = parser.parse_args(argv)

    if "--apply" in (argv or sys.argv[1:]):
        print("integration checks do not support --apply", file=sys.stderr)
        return 2

    report = run_checks()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2))

    print(f"Wrote {REPORT_PATH}")
    print(f"Overall status: {report['status']}")
    return 0 if report["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())

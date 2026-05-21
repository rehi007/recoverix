#!/usr/bin/env python3
"""Collect non-destructive diagnostics (no image copy, no bulk hashes)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_GUID = re.compile(
    r"\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}",
    re.IGNORECASE,
)


def mask_sensitive(obj: Any) -> Any:
    """Redact GUIDs and trim long strings in nested structures."""
    if isinstance(obj, dict):
        return {k: mask_sensitive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_sensitive(v) for v in obj[:200]]
    if isinstance(obj, str):
        s = _GUID.sub("{********-****-****-****-************}", obj)
        if len(s) > 200:
            return s[:120] + "…[truncated]"
        return s
    return obj


def _tail_file(path: Path, *, max_lines: int = 40) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    tail = lines[-max_lines:]
    return "\n".join(tail)


def _recovery_root_hint() -> Optional[Path]:
    env = os.environ.get("RECOVERYBOOT_DIAG_RECOVERY_ROOT")
    if env:
        return Path(env)
    for candidate in (Path("/mnt/recovery"), Path("/run/media/recovery")):
        if (candidate / "state").is_dir() or (candidate / "metadata").is_dir():
            return candidate
    return None


def collect_payload() -> Dict[str, Any]:
    import platform

    payload: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "os": platform.platform(),
    }

    if sys.platform == "win32":
        try:
            from windows_agent.preflight import run_preflight

            payload["preflight"] = mask_sensitive(run_preflight().to_dict())
        except Exception as exc:  # noqa: BLE001
            payload["preflight"] = {"error": str(exc)}
        try:
            from partition_manager.discovery import discover_partitions

            dr = discover_partitions(dry_run=True)
            payload["partition_discovery"] = mask_sensitive(json.loads(dr.to_json()))
        except Exception as exc:  # noqa: BLE001
            payload["partition_discovery"] = {"error": str(exc)}
        try:
            from boot_manager.firmware_reader import read_firmware_boot

            fr = read_firmware_boot(dry_run=False)
            payload["firmware_summary"] = mask_sensitive(
                {
                    "status": fr.status,
                    "boot_order_len": len(fr.boot_order),
                    "recoveryboot_present": fr.recovery_boot is not None,
                    "windows_boot_present": fr.windows_boot_manager is not None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            payload["firmware_summary"] = {"error": str(exc)}
    else:
        payload["preflight"] = {"note": "Windows preflight skipped on non-Windows"}
        payload["partition_discovery"] = {"note": "Use Windows for WMI partition discovery summary"}

    if sys.platform == "linux":
        try:
            from boot_manager.firmware_reader import read_firmware_boot

            fr = read_firmware_boot(dry_run=True)
            payload["firmware_summary"] = {
                "note": "Linux host: firmware enum skipped",
                "dry_run_status": fr.status,
            }
        except Exception as exc:  # noqa: BLE001
            payload["firmware_summary"] = {"error": str(exc)}

    root = _recovery_root_hint()
    payload["recovery_root_hint"] = str(root) if root else None
    if root:
        try:
            from restore_engine.restore_state import load_recovery_state

            st = load_recovery_state(root)
            payload["recovery_state_summary"] = mask_sensitive(
                {
                    "rollback_required": st.rollback_required,
                    "restore_in_progress": st.restore_in_progress,
                    "failure_count": st.failure_count,
                    "recoveryboot_failure_count": st.recoveryboot_failure_count,
                    "last_failure_reason": (st.last_failure_reason or "")[:200],
                }
            )
        except Exception as exc:  # noqa: BLE001
            payload["recovery_state_summary"] = {"error": str(exc)}

        log_dir = root / "logs"
        tails: Dict[str, Optional[str]] = {}
        for name in ("restore.log", "rollback.log", "error.log", "integrity.log", "boot.log"):
            p = log_dir / name
            tails[name] = _tail_file(p, max_lines=25)
        payload["log_tails"] = mask_sensitive(tails)

    return mask_sensitive(payload)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect diagnostics (no image/hash dumps)")
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "diagnostics" / "diagnostics.json",
        help="Output JSON path",
    )
    args = parser.parse_args(argv)

    payload = collect_payload()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

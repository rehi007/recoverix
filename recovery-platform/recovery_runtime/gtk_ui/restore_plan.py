"""Restore dry-run plan generation (simulation only; no restore execution)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from backup_engine.backup_state import DEFAULT_IMAGE_FILES
from backup_engine.backup_planner import discover_layout
from backup_engine.manifest import MANIFEST_FILENAME, load_recovery_manifest
from common.command import run_readonly
from recovery_runtime.gtk_ui.logging_util import restore_plan_log
from recovery_runtime.gtk_ui.restore_preflight import (
    DEV_MODE_DISABLED_MSG,
    development_mode_enabled,
    run_restore_preflight,
)
from restore_engine.partclone_restore import (
    format_partclone_fat_restore_command,
    format_partclone_ntfs_restore_command,
)


@dataclass
class RestoreTargetPlan:
    """Single restore target (planned only; not executed)."""

    name: str
    source: str
    target: str
    filesystem: str
    operation: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class RestorePlanResult:
    """Dry-run restore plan (JSON-serializable)."""

    restore_plan_ok: bool
    development_mode: bool
    restore_execution_enabled: bool
    targets: List[Dict[str, str]] = field(default_factory=list)
    dry_run_commands: List[str] = field(default_factory=list)
    restore_order: List[str] = field(default_factory=list)
    overwrite_targets: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_json_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json_dict(), indent=indent, sort_keys=True)


def _resolve_target_path(device: str) -> str:
    """Prefer /dev/disk/by-partuuid/ when PARTUUID is available."""
    result = run_readonly(["blkid", "-o", "value", "-s", "PARTUUID", device])
    if result.returncode == 0:
        partuuid = (result.stdout or "").strip().strip("{}")
        if partuuid:
            by_partuuid = Path(f"/dev/disk/by-partuuid/{partuuid}")
            if by_partuuid.exists():
                restore_plan_log(f"target discovery: {device} -> {by_partuuid}")
                return str(by_partuuid)
    restore_plan_log(f"target discovery: using device path {device}")
    return device


def _image_paths(recovery_root: Path) -> Dict[str, Path]:
    return {key: recovery_root / rel for key, rel in DEFAULT_IMAGE_FILES.items()}


def build_restore_plan_summary(
    plan: RestorePlanResult,
    *,
    preflight_ok: Optional[bool] = None,
) -> str:
    """Human-readable summary for GTK dry-run dialog."""
    lines = [
        "Restore dry-run plan",
        "",
        f"- preflight OK: {'yes' if preflight_ok else 'no' if preflight_ok is not None else 'n/a'}",
        f"- restore plan OK: {'yes' if plan.restore_plan_ok else 'no'}",
        f"- restore execution enabled: {'yes' if plan.restore_execution_enabled else 'no'}",
        f"- development mode: {'yes' if plan.development_mode else 'no'}",
    ]
    if plan.restore_order:
        lines.append("")
        lines.append("restore order:")
        for idx, step in enumerate(plan.restore_order, start=1):
            lines.append(f"  {idx}. {step}")

    if plan.targets:
        lines.append("")
        lines.append("restore targets:")
        for target in plan.targets:
            lines.append(f"  - {target['name']}: {target['operation']}")
            lines.append(f"      source: {target['source']}")
            lines.append(f"      target: {target['target']} ({target['filesystem']})")

    if plan.overwrite_targets:
        lines.append("")
        lines.append("overwrite targets (simulation):")
        for item in plan.overwrite_targets:
            lines.append(f"  - {item}")

    if plan.dry_run_commands:
        lines.append("")
        lines.append("dry-run restore commands:")
        for cmd in plan.dry_run_commands:
            lines.append(f"  - {cmd}")

    if plan.errors:
        lines.append("")
        lines.append("errors:")
        for err in plan.errors:
            lines.append(f"  - {err}")
    if plan.warnings:
        lines.append("")
        lines.append("warnings:")
        for warn in plan.warnings:
            lines.append(f"  - {warn}")
    return "\n".join(lines)


def run_restore_plan(*, require_preflight_ok: bool = True) -> RestorePlanResult:
    """Build a non-destructive restore dry-run plan."""
    dev_mode = development_mode_enabled()
    warnings: List[str] = [DEV_MODE_DISABLED_MSG] if dev_mode else []
    errors: List[str] = []

    restore_plan_log("restore plan generation start")

    result = RestorePlanResult(
        restore_plan_ok=False,
        development_mode=dev_mode,
        restore_execution_enabled=False,
        warnings=list(warnings),
    )

    preflight = run_restore_preflight()
    restore_plan_log(
        f"preflight coupling: ok={preflight.restore_preflight_ok} "
        f"errors={len(preflight.errors)}"
    )
    if require_preflight_ok and not preflight.restore_preflight_ok:
        errors.extend(preflight.errors)
        result.errors = errors
        restore_plan_log("restore plan blocked: preflight failed")
        return result

    if not preflight.recovery_image_mount:
        errors.append("RECOVERY_IMAGE mount point unknown")
        result.errors = errors
        return result

    recovery_root = Path(preflight.recovery_image_mount)
    topology_reason, layout = discover_layout()
    if topology_reason or layout is None:
        errors.append(topology_reason or "layout discovery failed")
        result.errors = errors
        restore_plan_log(f"unsupported topology: {topology_reason}")
        return result

    images = _image_paths(recovery_root)
    try:
        manifest = load_recovery_manifest(recovery_root)
        restore_plan_log(
            f"manifest parsing: ok device_id={manifest.get('device_id', 'unknown')}"
        )
    except FileNotFoundError:
        errors.append("manifests/recovery-manifest.json missing")
        restore_plan_log("manifest parsing: missing")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"manifest parsing failed: {exc}")
        restore_plan_log(f"manifest parsing failed: {exc}")

    for key, path in images.items():
        if not path.is_file():
            errors.append(f"missing image: {path.relative_to(recovery_root)}")
            restore_plan_log(f"image check: missing {path}")

    if errors:
        result.errors = errors
        restore_plan_log(f"restore plan blocked: {len(errors)} error(s)")
        return result

    efi_target = _resolve_target_path(layout.efi.path)
    windows_target = _resolve_target_path(layout.windows.path)

    efi_source = str(images["efi"])
    windows_source = str(images["windows"])

    efi_cmd = format_partclone_fat_restore_command(Path(efi_source), efi_target)
    windows_cmd = format_partclone_ntfs_restore_command(Path(windows_source), windows_target)

    targets = [
        RestoreTargetPlan(
            name="EFI",
            source=efi_source,
            target=efi_target,
            filesystem="vfat",
            operation="partclone.fat restore",
        ),
        RestoreTargetPlan(
            name="Windows",
            source=windows_source,
            target=windows_target,
            filesystem="ntfs",
            operation="partclone.ntfs restore",
        ),
    ]

    restore_order = [
        "validate backup manifest and hashes (read-only)",
        "simulate EFI restore from images/efi_backup.pcl (no write)",
        "simulate Windows restore from images/windows_backup.pcl (no write)",
        "bootability validation (planned; not executed in development mode)",
    ]

    overwrite_targets = [
        f"{efi_target} (EFI System Partition contents)",
        f"{windows_target} (Windows NTFS volume contents)",
    ]

    dry_run_commands = [efi_cmd, windows_cmd]

    result.targets = [t.to_dict() for t in targets]
    result.dry_run_commands = dry_run_commands
    result.restore_order = restore_order
    result.overwrite_targets = overwrite_targets
    result.restore_plan_ok = True
    result.restore_execution_enabled = False
    result.errors = errors
    result.warnings = warnings

    restore_plan_log("restore order: " + " -> ".join(restore_order))
    for cmd in dry_run_commands:
        restore_plan_log(f"dry-run command: {cmd}")
    restore_plan_log(
        f"restore plan complete ok={result.restore_plan_ok} "
        f"restore_execution_enabled={result.restore_execution_enabled}"
    )
    for warn in warnings:
        restore_plan_log(f"warning: {warn}")

    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for recoverix-restore-plan."""
    _ = argv
    result = run_restore_plan()
    print(result.to_json())
    return 0 if result.restore_plan_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

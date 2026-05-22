"""Menu action wrappers with validation, confirmation, and safe error handling."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from backup_engine.backup_planner import create_backup_plan
from backup_engine.run_backup import plan_backup_run, run_backup
from backup_engine.write_guard import WriteGuard
from common.errors import (
    BitLockerActiveError,
    ConfirmationRequiredError,
    InvalidConfirmationPhraseError,
    RestoreEnvironmentError,
    RestoreSafetyError,
)
from common.logger import get_logger
from restore_engine.confirmation import RESTORE_CONFIRMATION_PHRASE
from restore_engine.restore_executor import RestoreExecutor, build_execution_context
from restore_engine.restore_safety import authorize_restore_execution
from restore_engine.restore_state import (
    RecoveryState,
    load_recovery_state,
    logs_dir,
    save_recovery_state,
)
from recovery_runtime.runtime_context import RuntimeContext
from recovery_runtime.ui_helpers import (
    BACKUP_CONFIRMATION_PHRASE,
    DELETE_CONFIRMATION_PHRASE,
    append_error_log,
    format_bytes,
    prompt_phrase,
    prompt_yes_no,
    render_log_menu,
    verify_phrase,
)

logger = get_logger(__name__)

LOG_FILES = (
    "restore.log",
    "rollback.log",
    "error.log",
    "integrity.log",
    "boot.log",
)

DELETE_TARGETS = (
    "images",
    "metadata",
    "logs",
    "state",
)

DELETE_FILES = (
    "recovery-manifest.json",
    "recovery-manifest.sha256",
)


def _safe_action(
    ctx: RuntimeContext,
    *,
    action_name: str,
    operation: Callable[[], str],
) -> str:
    try:
        return operation()
    except (ConfirmationRequiredError, InvalidConfirmationPhraseError) as exc:
        msg = str(exc)
        _log_ui_error(ctx, f"{action_name}: {msg}")
        return msg
    except (RestoreSafetyError, RestoreEnvironmentError, BitLockerActiveError) as exc:
        msg = str(exc)
        _log_ui_error(ctx, f"{action_name}: {msg}")
        return msg
    except Exception as exc:
        logger.exception("%s failed", action_name)
        msg = f"{action_name} 실패: {exc}"
        _log_ui_error(ctx, msg)
        return msg


def _log_ui_error(ctx: RuntimeContext, message: str) -> None:
    if ctx.recovery_root:
        append_error_log(ctx.recovery_root, message)
    ctx.runtime_state.last_message = message


def show_status_action(ctx: RuntimeContext) -> str:
    def _run() -> str:
        ctx.refresh()
        lines = list(ctx.status_lines)
        if ctx.menu.backup_reason:
            lines.append(f"Backup menu   : disabled ({ctx.menu.backup_reason})")
        else:
            lines.append("Backup menu   : executable")
        if ctx.menu.restore_reason:
            lines.append(f"Restore menu  : disabled ({ctx.menu.restore_reason})")
        else:
            lines.append("Restore menu  : executable")
        if ctx.menu.backup_warning:
            lines.append(f"Backup warning: {ctx.menu.backup_warning}")
        if ctx.firmware_state:
            boot_plan = None
            try:
                from boot_manager.bootorder_planner import plan_bootorder_recovery

                boot_plan = plan_bootorder_recovery(ctx.firmware_state, dry_run=True)
            except Exception:
                boot_plan = None
            if boot_plan:
                lines.append(f"BootOrder plan : {boot_plan.status}")
        return "시스템 상태:\n" + "\n".join(f"  - {line}" for line in lines)

    return _safe_action(ctx, action_name="status", operation=_run)


def run_backup_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if not ctx.menu.backup_executable:
            return f"백업 실행 불가: {ctx.menu.backup_reason or 'disabled'}"
        if ctx.recovery_root is None:
            return "RECOVERY_IMAGE가 마운트되지 않았습니다."

        plan = create_backup_plan()
        dry_plan = plan_backup_run(WriteGuard(apply=False, confirmed=False))

        print("\n=== 백업 사전 점검 ===")
        print(f"상태: {plan.status}")
        if plan.reason:
            print(f"사유: {plan.reason}")
        if dry_plan.estimated_required_bytes:
            print(
                f"예상 backup 크기: {format_bytes(dry_plan.estimated_required_bytes)} "
                f"({dry_plan.estimated_required_gb} GiB planned)"
            )
        if dry_plan.estimation_method:
            print(f"용량 추정 방식: {dry_plan.estimation_method}")
        if dry_plan.estimated_used_bytes:
            print(f"Windows 사용량(추정): {format_bytes(dry_plan.estimated_used_bytes)}")
        if dry_plan.estimation_warning:
            print(f"용량 추정 경고: {dry_plan.estimation_warning}")
        if dry_plan.reason and dry_plan.estimation_method == "partition_size_fallback":
            print(f"추정 사유: {dry_plan.reason}")
        if ctx.recovery_image_partition and ctx.recovery_image_partition.size:
            print(
                f"Recovery Image 크기: {format_bytes(ctx.recovery_image_partition.size)}"
            )
        if ctx.menu.backup_warning:
            print(f"경고: {ctx.menu.backup_warning}")

        if not prompt_yes_no("백업을 진행하시겠습니까?", input_func=input_func):
            return "백업이 취소되었습니다."

        phrase = prompt_phrase(
            BACKUP_CONFIRMATION_PHRASE,
            description="백업 실행 확인 문구를 입력하세요.",
            input_func=input_func,
        )
        if not verify_phrase(phrase, BACKUP_CONFIRMATION_PHRASE):
            return "confirmation phrase 불일치 — 백업이 차단되었습니다."

        result = run_backup(apply=True, confirmed=True)
        ctx.refresh()
        if result.status == "COMPLETED":
            return "백업이 완료되었습니다."
        return f"백업 실패: {result.reason or result.status}"

    return _safe_action(ctx, action_name="backup", operation=_run)


def run_restore_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if not ctx.menu.restore_executable:
            return f"복구 실행 불가: {ctx.menu.restore_reason or 'disabled'}"
        if ctx.recovery_root is None or ctx.current_disk is None:
            return "복구에 필요한 디스크 정보를 확인할 수 없습니다."

        ctx.refresh()
        plan = ctx.restore_plan
        print("\n=== 복구 사전 검증 ===")
        if ctx.validation_result:
            print(
                f"validate_restore: {'PASS' if ctx.validation_result.allowed else 'FAIL'}"
            )
            if ctx.validation_result.reason:
                print(f"  {ctx.validation_result.reason}")
        if plan:
            print(f"restore_allowed: {plan.restore_allowed}")
            print(f"EFI rollback 가능: {'yes' if ctx.efi_rollback_available else 'no'}")
            if plan.target_disk:
                for key, value in plan.target_disk.items():
                    if isinstance(value, dict):
                        print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
                    else:
                        print(f"  {key}: {value}")
            if plan.target_partitions:
                print("대상 파티션:")
                for name, info in plan.target_partitions.items():
                    print(f"  {name}: {info.get('path', info)}")

        for line in ctx.status_lines:
            if "Windows Boot" in line:
                print(line)

        phrase = prompt_phrase(
            RESTORE_CONFIRMATION_PHRASE,
            description="복구 실행 확인 문구를 입력하세요.",
            input_func=input_func,
        )
        if not verify_phrase(phrase, RESTORE_CONFIRMATION_PHRASE):
            return "confirmation phrase 불일치 — 복구가 차단되었습니다."

        safety = authorize_restore_execution(
            apply=True,
            confirmed=True,
            confirmation_phrase=phrase,
            recovery_root=ctx.recovery_root,
        )
        if not safety.allowed:
            return safety.reason or "restore not authorized"

        exec_ctx = build_execution_context(
            safety,
            confirmed=True,
            recovery_root=ctx.recovery_root,
            runtime_state=ctx.runtime_state,
        )
        result = RestoreExecutor(exec_ctx).execute()
        ctx.refresh()
        if result.success:
            return "복구가 완료되었습니다."
        return f"복구 실패: {result.reason or result.status}"

    return _safe_action(ctx, action_name="restore", operation=_run)


def delete_backup_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if not ctx.menu.delete_executable:
            return f"백업 삭제 불가: {ctx.menu.delete_reason or 'disabled'}"
        root = ctx.recovery_root
        if root is None:
            return "RECOVERY_IMAGE가 마운트되지 않았습니다."

        print("\n=== 기존 백업 삭제 ===")
        print("삭제 대상: images/, metadata/, logs/, state/, manifest files")
        print("관리자 승인 정책: placeholder (추후 enterprise policy 연동)")

        if not prompt_yes_no("백업 데이터를 삭제하시겠습니까?", input_func=input_func):
            return "백업 삭제가 취소되었습니다."

        phrase = prompt_phrase(
            DELETE_CONFIRMATION_PHRASE,
            description="백업 삭제 확인 문구를 입력하세요.",
            input_func=input_func,
        )
        if not verify_phrase(phrase, DELETE_CONFIRMATION_PHRASE):
            return "confirmation phrase 불일치 — 삭제가 차단되었습니다."

        _write_audit_log(root, "backup delete requested")
        staging = root / ".delete_staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

        try:
            paths = _collect_delete_paths(root)
            if not paths:
                return "삭제할 백업 데이터가 없습니다."
            staging.mkdir(parents=True, exist_ok=True)
            for path in paths:
                rel = path.relative_to(root)
                dest = staging / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if path.is_dir():
                    shutil.copytree(path, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(path, dest)
            for path in paths:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.is_file():
                    path.unlink()
            _mark_no_valid_recovery_image(root)
            shutil.rmtree(staging, ignore_errors=True)
            ctx.refresh()
            return "백업 데이터가 삭제되었습니다 (NO_VALID_RECOVERY_IMAGE)."
        except Exception as exc:
            logger.exception("backup delete failed; attempting rollback")
            _rollback_delete(staging, root)
            state = load_recovery_state(root)
            state.rollback_required = True
            state.last_failure_reason = str(exc)
            save_recovery_state(root, state)
            _write_audit_log(root, f"backup delete failed: {exc}")
            raise

    return _safe_action(ctx, action_name="delete_backup", operation=_run)


def show_logs_action(
    ctx: RuntimeContext,
    *,
    input_func: Callable[[str], str],
) -> str:
    def _run() -> str:
        if ctx.recovery_root is None:
            return "RECOVERY_IMAGE가 마운트되지 않아 로그를 표시할 수 없습니다."
        return render_log_menu(ctx.recovery_root, LOG_FILES, input_func=input_func)

    return _safe_action(ctx, action_name="logs", operation=_run)


def _collect_delete_paths(root: Path) -> List[Path]:
    paths: List[Path] = []
    for relative in DELETE_TARGETS:
        path = root / relative
        if path.exists():
            paths.append(path)
    for relative in DELETE_FILES:
        path = root / relative
        if path.is_file():
            paths.append(path)
    marker = root / "state" / "incomplete_backup"
    if marker.is_file():
        paths.append(marker)
    return paths


def _rollback_delete(staging: Path, root: Path) -> None:
    if not staging.is_dir():
        return
    for item in staging.rglob("*"):
        if item.is_dir():
            continue
        rel = item.relative_to(staging)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
    for item in staging.iterdir():
        if item.is_dir():
            dest = root / item.name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest, dirs_exist_ok=True)


def _mark_no_valid_recovery_image(root: Path) -> None:
    state = load_recovery_state(root)
    state.current_stage = "NO_VALID_RECOVERY_IMAGE"
    state.restore_success = False
    state.rollback_required = False
    state.restore_in_progress = False
    state.last_failure_reason = None
    save_recovery_state(root, state)


def _write_audit_log(root: Path, message: str) -> None:
    try:
        log_dir = logs_dir(root)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "audit.log"
        stamp = datetime.now(timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} | {message}\n")
    except OSError:
        pass

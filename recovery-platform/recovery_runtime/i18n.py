"""Korean UI text helpers for the recovery runtime.

System identifiers such as RECOVERY_IMAGE, device paths, EFI/GPT/NTFS, and log
filenames intentionally remain unchanged. These helpers translate user-facing
TUI labels, status values, reasons, and progress messages.
"""

from __future__ import annotations

from typing import Any


LABELS = {
    "Recovery Image": "복구 이미지",
    "Recovery Linux": "복구 Linux",
    "Manifest": "매니페스트",
    "Restore": "복구",
    "Backup": "백업",
    "Rollback": "롤백",
    "Restore active": "복구 진행 중",
    "Interrupted": "중단된 복구",
    "Windows-first": "Windows 우선 부팅",
    "Reboot loop": "재부팅 루프",
    "Manifest path": "매니페스트 경로",
    "Last failure": "마지막 실패",
    "Stage": "단계",
    "Last message": "마지막 메시지",
    "Valid backup": "백업 이미지",
    "Incomplete mark": "미완료 백업 표시",
    "validate_restore": "복구 검증",
    "restore_allowed": "복구 가능 여부",
    "restore_mode": "복구 모드",
    "rollback_required": "롤백 필요",
    "restore_in_progress": "복구 진행 중",
    "Windows Boot Mgr": "Windows 부팅 관리자",
    "Secure Boot": "Secure Boot",
    "BitLocker": "BitLocker",
    "BootOrder": "부팅 순서",
    "BootOrder plan": "부팅 순서 계획",
    "BootOrder reason": "부팅 순서 사유",
    "Runtime volumes": "런타임 볼륨",
    "Recovery state": "복구 상태",
    "Boot status": "부팅 상태",
    "Menu state": "메뉴 상태",
    "Backup menu": "백업 메뉴",
    "Restore menu": "복구 메뉴",
    "Backup warning": "백업 경고",
    "message": "메시지",
    "Backup plan": "백업 계획",
    "Storage estimate": "저장공간 예상",
    "Recovery image": "복구 이미지",
    "status": "상태",
    "reason": "사유",
    "estimated size": "예상 크기",
    "estimation method": "예상 방식",
    "windows usage": "Windows 사용량",
    "warning": "경고",
    "free space": "여유 공간",
    "partition size": "파티션 크기",
    "Restore validation": "복구 검증",
    "validate_restore": "복구 검증",
    "EFI rollback": "EFI 롤백",
    "disk_path": "디스크 경로",
    "disk_model": "디스크 모델",
    "disk_serial": "디스크 시리얼",
    "disk_size": "디스크 크기",
    "disk_guid": "디스크 GUID",
    "windows_uuid": "Windows UUID",
    "efi_uuid": "EFI UUID",
    "Backup information": "백업 정보",
    "backup type": "백업 종류",
    "restore baseline": "복구 기준 크기",
    "manifest": "매니페스트",
    "Recovery status": "복구 상태",
    "Status": "상태",
    "execution": "실행 가능 여부",
    "Partition plan": "파티션 계획",
    "Windows partition": "Windows 파티션",
    "current size": "현재 크기",
    "required size": "복구 필요 크기",
    "restore margin": "확장 여유공간",
    "available after C": "C 뒤 여유공간",
    "target size": "확장 목표 크기",
    "next partition": "다음 파티션",
    "Safety policy": "안전 정책",
    "partition move": "파티션 이동",
    "free space": "여유 공간",
    "NTFS resize": "NTFS 크기 조정",
    "GPT backup": "GPT 백업",
    "Target disk": "대상 디스크",
    "Target partitions": "대상 파티션",
    "Compatibility": "호환성",
    "method": "방식",
    "source NTFS": "원본 NTFS",
    "target Windows": "대상 Windows",
    "used bytes": "사용량",
    "size deficit": "부족한 크기",
    "used range": "사용 영역",
    "Delete scope": "삭제 범위",
    "targets": "대상",
    "policy": "정책",
    "Current lock state": "현재 잠금 상태",
    "rollback required": "롤백 필요",
    "restore in progress": "복구 진행 중",
    "current stage": "현재 단계",
    "last failure": "마지막 실패",
    "Preserved data": "유지되는 데이터",
    "backup image": "백업 이미지",
    "logs": "로그",
    "Reboot target": "재부팅 대상",
    "target": "대상",
    "action": "동작",
    "windows": "Windows",
    "efi": "EFI",
    "recovery_linux": "RECOVERY_LINUX",
    "recovery_image": "RECOVERY_IMAGE",
}

VALUES = {
    "unknown": "알 수 없음",
    "not found": "없음",
    "found": "있음",
    "missing": "없음",
    "enabled": "사용 가능",
    "disabled": "비활성화",
    "executable": "실행 가능",
    "available": "가능",
    "not available": "사용 불가",
    "yes": "예",
    "no": "아니요",
    "true": "예",
    "false": "아니요",
    "required": "필요",
    "not required": "필요 없음",
    "risk": "위험",
    "ok": "정상",
    "none": "없음",
    "preserved": "유지됨",
    "standard": "표준",
    "standard backup": "표준 백업",
    "compact backup": "compact 백업",
    "unsupported backup image": "지원하지 않는 백업 이미지",
    "PASS": "통과",
    "FAIL": "실패",
    "PLANNED": "준비됨",
    "REJECTED": "차단됨",
    "MOUNT_REQUIRED": "마운트 필요",
    "SKIPPED": "건너뜀",
    "UNKNOWN": "알 수 없음",
    "ON": "켜짐",
    "OFF": "꺼짐",
    "never": "수행하지 않음",
    "not performed": "수행하지 않음",
    "local confirmation required": "로컬 확인 필요",
    "images/, metadata/, logs/, state/, manifest files": (
        "images/, metadata/, logs/, state/, 매니페스트 파일"
    ),
    "restart this computer and boot into Windows": "이 컴퓨터를 재시작하고 Windows로 부팅",
    "Windows": "Windows",
}

REASONS = {
    "System Status": "시스템 상태",
    "Continue?": "계속 진행하시겠습니까?",
    "valid backup already exists": "백업 이미지가 이미 있음",
    "unsupported backup image detected": "지원하지 않는 백업 이미지가 감지됨",
    "Unsupported backup image detected. Delete this backup image and create a new standard backup.": (
        "지원하지 않는 백업 이미지가 감지되었습니다. 이 백업 이미지를 삭제하고 새 표준 백업을 생성하세요."
    ),
    "no valid backup": "유효한 백업 이미지 없음",
    "RECOVERY_IMAGE partition not found": "RECOVERY_IMAGE 파티션을 찾을 수 없음",
    "RECOVERY_LINUX partition not found": "RECOVERY_LINUX 파티션을 찾을 수 없음",
    "RECOVERY_IMAGE not mounted": "RECOVERY_IMAGE가 마운트되지 않음",
    "BitLocker ON": "BitLocker가 켜져 있음",
    "backup layout discovery failed": "백업 대상 디스크 구조 확인 실패",
    "incomplete_backup marker present": "미완료 백업 표시가 남아 있음",
    "rollback_required=true": "복구 실패 잠금 해제가 필요함",
    "interrupted restore detected": "중단된 복구 작업이 감지됨",
    "interrupted restore detected; restore disabled": "중단된 복구 작업이 감지되어 복구가 비활성화됨",
    "rollback required before another restore": "다음 복구 전에 복구 실패 잠금 해제가 필요함",
    "reboot loop prevention active (Windows-first required)": "재부팅 루프 방지가 활성화됨(Windows 우선 부팅 필요)",
    "restore_in_progress=true": "복구 작업이 진행 중으로 표시됨",
    "restore not allowed": "복구가 허용되지 않음",
    "restore plan unavailable": "복구 계획을 확인할 수 없음",
    "validation failed": "검증 실패",
    "manifest hash mismatch": "매니페스트 해시 불일치",
    "device_id mismatch": "디스크 식별자 불일치",
    "incomplete backup detected": "미완료 백업 감지",
    "RECOVERY_IMAGE is not mounted.": "RECOVERY_IMAGE가 마운트되지 않았습니다.",
    "Privileged runtime helper is missing.": "권한 helper를 사용할 수 없습니다.",
    "Rebooting to Windows.": "Windows로 재부팅합니다.",
}

PROGRESS_MESSAGES = {
    "Preparing backup workspace...": "백업 작업공간 준비 중",
    "Preparing backup workspace": "백업 작업공간 준비 중",
    "Backing up Windows partition...": "Windows 파티션 백업 중",
    "Backing up Windows partition": "Windows 파티션 백업 중",
    "Verifying Windows backup image": "Windows 백업 이미지 확인 중",
    "Backing up EFI partition": "EFI 파티션 백업 중",
    "Verifying EFI backup image": "EFI 백업 이미지 확인 중",
    "Backing up GPT metadata": "GPT 메타데이터 백업 중",
    "Verifying GPT metadata backup": "GPT 메타데이터 백업 확인 중",
    "Collecting backup metadata": "백업 메타데이터 수집 중",
    "Generating backup hashes": "백업 해시 생성 중",
    "Verifying backup hashes": "백업 해시 확인 중",
    "Finalizing backup metadata": "백업 메타데이터 마무리 중",
    "Recovery backup completed.": "복구 백업 완료",
    "Recovery backup completed": "복구 백업 완료",
    "Checking restore readiness": "복구 준비 상태 확인 중",
    "Restore readiness verified": "복구 준비 상태 확인 완료",
    "Backing up EFI safety files": "EFI 안전 파일 백업 중",
    "Preparing Windows partition": "Windows 파티션 준비 중",
    "Windows partition restored": "Windows 파티션 복구 완료",
    "Finalizing system restore": "시스템 복구 마무리 중",
    "Restoring RecoveryBoot EFI files": "RecoveryBoot EFI 파일 복구 중",
    "Verifying Windows Boot Manager": "Windows 부팅 관리자 확인 중",
    "Checking RecoveryBoot order": "RecoveryBoot 부팅 순서 확인 중",
    "System restore completed.": "시스템 복구 완료",
    "System restore completed": "시스템 복구 완료",
    "Restoring Windows partition": "Windows 파티션 복구 중",
    "Starting system restore": "시스템 복구 시작 중",
    "Validating restore image": "복구 이미지 검증 중",
    "Restore image validation completed": "복구 이미지 검증 완료",
}


def ko_label(label: Any) -> str:
    text = str(label or "").strip()
    return LABELS.get(text, text)


def ko_value(value: Any) -> str:
    if value is None or value == "":
        return VALUES["unknown"]
    text = str(value)
    stripped = text.strip()
    return VALUES.get(stripped, VALUES.get(stripped.lower(), text))


def ko_reason(reason: Any) -> str:
    if reason is None or reason == "":
        return VALUES["unknown"]
    text = str(reason)
    stripped = text.strip()
    if stripped in REASONS:
        return REASONS[stripped]
    lowered = stripped.lower()
    for source, translated in REASONS.items():
        if source.lower() in lowered:
            return translated
    if "target windows partition is smaller" in lowered:
        return "대상 Windows 파티션 크기가 백업 이미지 요구 크기보다 작습니다."
    if "recovery_image must be mounted" in lowered:
        return "RECOVERY_IMAGE를 읽기 전용으로 마운트해야 합니다."
    if "no adjacent free space" in lowered:
        return "Windows 파티션 바로 뒤에 연속된 여유 공간이 없습니다."
    if "already large enough" in lowered:
        return "Windows 파티션은 이미 복구 가능한 크기입니다."
    translated_value = ko_value(stripped)
    return translated_value if translated_value != stripped else text


def ko_progress(message: Any) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    return PROGRESS_MESSAGES.get(text, text)


def ko_status_line(line: str) -> str:
    if ":" not in line:
        return ko_reason(line)
    prefix = ""
    stripped = line
    while stripped.startswith(" "):
        prefix += " "
        stripped = stripped[1:]
    label, value = stripped.split(":", 1)
    translated_label = ko_label(label.strip())
    translated_value = ko_reason(value.strip())
    return f"{prefix}{translated_label:<14}: {translated_value}"


def ko_disabled_suffix(reason: Any) -> str:
    return f" [비활성화: {ko_reason(reason)}]"

# 복구(Restore Engine)

> **⚠️ Destructive operation**  
> 복구는 대상 디스크·GPT·EFI 파티션·Windows 볼륨에 대해 **파괴적(destructive) 쓰기**를 유발합니다. 검증 단계 및 승인 절료(confirmation phrase·`--confirm` 등) 없이 실행하지 마십시오.

## 개요

Restore Engine은 **`validate_restore` 결과를 신뢰의 전제로** 두고, 실제 실행 구간에서는 별도의 안전 검사·복구 상태 관리와 연결됩니다. **플래너(restore_planner)** 는 **실행 불가**(시뮬레이션·계획만).

## 검증 플로우(FAIL CLOSED)

- 불일치·손실·무결성 실패 시 **복구 비활성화**
- 포함 예: **매니페스트 해시**, **이미지 해시**, **`device_id`/디스크 GUID**, **incomplete_backup**

실행 허용은 정책상 **통과 결과가 모두 명시적인데** 해당할 때입니다.

## Confirmation phrase 및 CLI 안전 장치

- phrase 불일치 → 실행 차단  
- `--apply`만 있거나 **`--confirm` 없음** → 실행 차단(참조 `run_restore` 구현 규격)

구체 플래그 조합은 **제품 명령행 도움말 및 구현 코드**를 최종 기준으로 합니다.

## Rollback 준비(설계 관점)

- GPT **사전 스냅샷** 저장
- EFI 쪽 **복구 전 백업** 채널(경로 및 도구 명은 구현·계획에 포함)

복구 실행 전 **롤백 재료** 존재를 검증에 반영하는 것을 권장합니다.

## GPT / EFI

- 플래너/실행 규격은 **실제 쓰기 없는 단계**(step 12 이전)에서는 계획 문자열만 생성합니다.
- **`bootmgfw.efi`**(Windows Boot Manager 진입 파일) 보호 규격은 플래너 문자열 및 주석 요약 반영 필수입니다.

## 실패 처리·상태 플래그

실패 후 **`rollback_required`**·**`restore_in_progress` 해제**(또는 오류 상태 기록)·**무한 자동 재시도 금지**은 [`FAILURE_RECOVERY.md`](FAILURE_RECOVERY.md)·[`ROLLBACK.md`](ROLLBACK.md)·상태 저장 경로 명세 참고입니다.

## 관련 문서

[`ROLLBACK.md`](ROLLBACK.md) · [`FAILURE_RECOVERY.md`](FAILURE_RECOVERY.md) · [`BITLOCKER_POLICY.md`](BITLOCKER_POLICY.md) · [`RECOVERY_RUNTIME.md`](RECOVERY_RUNTIME.md)

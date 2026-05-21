# 실패 처리·격리(Failure recovery policy)

## FAIL CLOSED

플랫폼 각 단계에서는 **증거가 부족하거나 상태가 불확실할 때 허용이 아니라 차단**을 선택합니다.

- 무결 검증 불일치
- 디바이스/매니페스트 불일치
- 불완전 백업(incomplete_backup)
- BitLocker 차단 상태로 쓰기 요구 발생

등은 **복구 채널을 열지 않습니다**.

## 검증 실패격리

- 사용자가 이유를 명확히 알 수 있는 **메시지**와 **로그**를 같은 실패 카테고리로 남깁니다(UI **visible≠executable** 정책과 정합성 유지).

## restore blocked 상태 요약

- `validate_restore != PASS` → 차단 유지  
- phrase/플래그 누락 등 **운영 정책 실패**

## `rollback_required`

- 장애·실패 경로 명시 상태로, 사용자·지원 채널이 **수동 절차**를 따르도록 유도합니다.
- 상태 전이 명세와 로그 채널은 [`ROLLBACK.md`](ROLLBACK.md)·[`LOGGING_POLICY.md`](LOGGING_POLICY.md) 참고입니다.

## interrupted restore

복구 처리 중 사용자 전원 종료 또는 커널 패닉 등은 **항상 상태 파일과 카운터**로 기록해야 하며 무한 자동 재시도로 이어져서는 안 됩니다.

## reboot loop 예방와 Windows 우선 순위

- BootOrder·카운터·BootNext 처리에 **복구 무한 진입**(부팅 루프)을 막는 완충 채택.
- 특히 **RecoveryBoot 진입 후 복구 런타임 실패 카운터** 초과 시 **Windows 경로 활성**(정책 문서 참고).

## 우선 순위 명시

예외 상 충돌 요약 순위:

1. **Windows 부팅 생존성**(복구 기능보다 먼저 기계가 사용자 작업 불가 상태로 버려지면 안 된다.)
2. **데이터 무결성(FAIL CLOSED)**
3. **복구 자동 진행 속도**(자동 반복 허용은 제한적으로만)

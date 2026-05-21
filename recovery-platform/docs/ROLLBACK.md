# 롤백(Rollback Engine)

## 목적

복구 과정 또는 복구 이후 재부팅에서 장애가 발생했을 때, **Windows가 다시 부팅 가능해지도록** 상태를 안전하게 보정하고, **복구 무한 재시도**를 막습니다.

## EFI 롤백

- 실행 경로에서는 복구 전 백업을 기준으로 **EFI 구역 복귀**(정책·도구는 구현 명세 참고)
- 실행 시에는 별도 검증 및 실패 카운터 정책과 연결됩니다.

## GPT 롤백

- 사전 GPT 스냅샷·매니페스트에 기재된 GPT 백업을 기준으로 **레이블/매칭 무결 검증 후** 진행해야 합니다.
- 잘못된 디스크 식별로 복귀 명령이 나가면 **전체 디스크 손상** 가능 → [`FAILURE_RECOVERY.md`](FAILURE_RECOVERY.md) 검증 규격 준수.

## interrupted restore 대응

- 중단·전원 차단 후 재부팅 시 **복구 진행 상태**를 식별해 **FAIL CLOSED** 또는 **복구 채널 재개 제한**(수동 채널)을 조합해야 합니다.
- `restore_in_progress` 등 상태 필드 처리 상세는 `recovery_state` 스키마·[`LOGGING_POLICY.md`](LOGGING_POLICY.md) 참고합니다.

## reboot loop 예방과 RecoveryBoot 반복 실패

- 특정 카운터(예: RecoveryBoot 진입 후 복구 런타임 실패)가 설정 임계에 도달하면 **Windows 우선**(Windows-first) 정책으로 Boot 순서 또는 정책 전환 가능.
- 참조 구현: **3회**(정책 값은 구현·문서 간 일치 검증 필요) 실패 후 Windows 우선 허브.

자동 무한 재시도는 허용되지 않습니다.

## 관련 문서

[`FAILURE_RECOVERY.md`](FAILURE_RECOVERY.md) · [`RECOVERYBOOT_POLICY.md`](RECOVERYBOOT_POLICY.md)

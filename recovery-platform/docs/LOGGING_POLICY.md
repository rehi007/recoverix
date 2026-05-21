# 로깅 정책 (Logging)

## 위치 요약

| 영역 | 경로 |
|------|------|
| Recovery Runtime / Recovery Image | **`RECOVERY_IMAGE`/logs/** (예: `restore`, `rollback`, `error`, `integrity`, `boot` 등) |
| Windows Agent | **`%ProgramData%\RecoveryBoot\logs\`** |

(`RECOVERY_IMAGE` 마운트 점은 디스커버리 결과에 따라 달라질 수 있습니다.)

## 주요 로그 종류

- **restore.log** — 복구 계획·실행 요약·결과
- **rollback.log** — 롤백 계획·실패 후 조치 요약
- **integrity.log** — 파일/해시 무결 관련 검사
- **repair.log**(Windows Agent) — BootOrder repair 계획·적용 줄
- **error.log** — UI/에이전트 예외 안전 처리 기록 등

실제 파일명 공간은 버전 간 일치해야 하며 필요 시 에이전트에 **audit**.log 등이 더해집니다.

## 로테이션

- 장기 파일 무한 증가 방지 위해 **파일 회전**(크기/보관 개수 한도)·압축 정책을 운영에 반영해야 합니다.  
  (Python `RotatingFileHandler` 등 참조 구현 사용 가능.)

## 로깅 실패 vs 복구 실패

로그 기록 실패 자체가 **허블 복구 성공 신호 변경이 아닙니다**(best-effort).

## 진단 제공 시

통합 진단 및 수집기는 로그를 **통째 복사·대량 업로드하지 않고 tail만** 수집하도록 제한합니다 — [`INTEGRATION_TESTS.md`](INTEGRATION_TESTS.md) 및 `collect_diagnostics` 설명 참고합니다.

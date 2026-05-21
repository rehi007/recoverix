# Rollback 테스트 계획

**목적:** Restore/EFI/Boot 관련 장애 시 **예상 동작·rollback·Windows survivability·로그**를 정의합니다.

**주의:** 아래 각 시나리오는 **전용 테스트 환경**에서 순차 검증합니다. 실패 주입 후에는 체크리스트로 시스템 상태를 초기화할 수 있는지 확인합니다.

---

## 공통 확인 (모든 행별)

| 항목 | 기록 위치 |
|------|-----------|
| 예상 동작 | 이 표 하단 각 시나리오 |
| rollback 동작 | `rollback_engine` 플래그·복구 상태 JSON·메뉴 메시지 |
| Windows 생존성 | bootmgfw / Windows Boot Manager 부팅 가능 여부 |
| 로그 | `RECOVERY_IMAGE/logs/` (`restore.log`, `rollback.log`, `error.log`, `integrity.log`, `audit.log` 등 배포판 기준) |

---

## 시나리오

### 1. Interrupted restore

- **주입:** restore 도중 재부팅·강제 종료·SSE 절단.
- **예상:** `rollback_required` 또는 `restore_in_progress` 등 상태 기록; 다음 부팅 시 메뉴에서 명확한 경고.
- **rollback:** 플래너 문서 및 코드 정책에 따른 단일 시도 GPT/EFI 롤백 (재시도 루프 없음 가정 확인).
- **Windows:** 우선 BIOS에서 Windows Boot Manager로 부팅 시도 (`EMERGENCY_BOOT_RECOVERY.md`).

### 2. EFI restore failure

- **주입:** ESP 마운트 실패 또는 스냅샷 불일치.
- **예상:** 단계별 abort; bootmgfw **덮어쓰기 금지** 정책 유지 확인.
- **rollback:** `EFI_RECOVERY_PLAN.md` 참고.

### 3. GPT rollback

- **주입:** 훼손된 GPT 백업 복구 시도 또는 의도적으로 잘못된 백업 파일 참조 (테스트 한정).
- **예상:** `--confirm`/정챱 게이트 없으면 실행 안 됨; 실패 시 단일 rollback 시도 후 중단.

### 4. Invalid manifest

- **주입:** JSON 손상·필드 삭제.
- **예상:** `validate_restore` **FAIL CLOSED**, restore 차단.

### 5. Hash mismatch

- **주입:** 이미지 또는 사이드카 해시 수정.
- **예상:** 검증 거절 및 이유 문자열 명시.

### 6. BootOrder drift

- **주입:** 펌웨어에서 순서 변경·에이전트 검사 알림 재현 (가능 환경).
- **예상:** Windows Agent / 계획기 dry-run 결과와 repair 정책 일치 확인.

### 7. RecoveryBoot failure count / drift

- **주입:** RecoveryBoot 진입 불가 또는 누락 상태 시뮬레이션(문서만으로는 하드웨어별 상이함).
- **예상:** 누락 시 repair 계획 또는 차단 조건 명시적 메시지; Windows 부트 우선 확인.

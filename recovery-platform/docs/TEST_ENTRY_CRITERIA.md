# 실제 파괴적 테스트(Destructive) 진입 기준

**정책:** 아래 **필수 조건 중 하나라도 FAIL이면 파괴적 backup/restore 테스트를 시작하지 않습니다.**  
자동 확인은 `tools/pre_destructive_gate.py`·`tools/final_release_gate.py`를 사용하고, 사람이 확인하는 항목은 체크리스트로 기록합니다.

## 자동·반자동(도구) 조건

| # | 조건 | 도구/근거 |
|---|------|-----------|
| 1 | `safety_audit` **PASS** 또는 **PASS_WITH_WARNINGS** | `tools/safety_audit.py` |
| 2 | `destructive_command_audit` **complete=true** | `safety_audit` 리포트 내 중첩 필드 |
| 3 | `pre_destructive_gate` **PASS** (또는 경고만 있는 **PASS_WITH_WARNINGS** — 운영자 검토) | `tools/pre_destructive_gate.py` |
| 4 | integration checks **PASS** 또는 **PASS_WITH_WARNINGS** | `tools/run_integration_checks.py --dry-run` |
| 5 | `rollback_required=false` | Recovery 상태 (게이트가 Linux+이미지 마운트 시 검사) |
| 6 | **incomplete_backup 없음** | 게이트·런타임 정책 |
| 7 | `validate_restore` **PASS** | 게이트 (런타임 컨텍스트) |
| 8 | **RecoveryBoot** 존재 (ESP 상 shim 경로) | 게이트 |
| 9 | **Windows Boot Manager** 존재·검증 **PASS** | 게이트 |
| 10 | **Recovery Runtime 정상 부팅** | 수동 확인 (메뉴·로그) |

## 운영자 필수(사람) 조건

| # | 조건 |
|---|------|
| 11 | 테스트 머신 **전체 데이터 백업** 완료(외부 스냅샷/이미지 등) |
| 12 | **테스트 전용 머신 또는 VM** (생산 PC 금지) |
| 13 | **BitLocker OFF** |
| 14 | **단일 Windows 디스크** 환경(정책상 multi-disk 미지원) |

## 패키징·저장소 수준 (최종 승인 시)

번호 밖 권장·필수:

- `scripts/build_release.py`로 번들 작성 후 **`scripts/validate_release.py`** **PASS**
- **`tools/final_release_gate.py` PASS** 후에만 “패키징 또는 현장 최초 적용” 최종 논의

## 한 줄 요약

> ** Gates + 체크리스트 전부 초록색이 될 때만** 최초 `--apply`/파괴적 복원을 허용한다.

## 참고

- [`PRE_DESTRUCTIVE_CHECKLIST.md`](PRE_DESTRUCTIVE_CHECKLIST.md)
- [`MANUAL_TEST_PLAN.md`](MANUAL_TEST_PLAN.md)
- [`EMERGENCY_BOOT_RECOVERY.md`](EMERGENCY_BOOT_RECOVERY.md)

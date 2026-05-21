# 수동 통합 테스트 계획 (Manual Test Plan, 20단계)

**범위:** Backup Engine, Restore Engine, Rollback Engine, Recovery Runtime, Windows Agent, Safety Audit, Integration Checks가 구현된 상태에서 **운영자가 수행하는** 검증·파괴적 테스트의 단계 정의.

**주의:** 이 문서는 **실제 destructive 동작을 대신 수행하지 않습니다.** 절차를 따르되, 각 단계의 명령·플래그는 해당 모듈 CLI 및 `docs/FIRST_*` 문서를 따릅니다.

---

## Phase 1 — 읽기 전용 검증

| 단계 | 목표 | 참고 |
|------|------|------|
| 1.1 | **Preflight** (권한·환경·BitLocker 경고 등) | Recovery Runtime 메뉴 / Windows Agent `--no-repair` |
| 1.2 | **Discovery** (레이아웃·볼륨·라벨) | `partition_manager` / `backup_planner` discovery 경로 |
| 1.3 | **Firmware reader** (가능한 환경에서만; Linux에선 제한적) | `boot_manager/firmware_reader` — Windows live 권장 |
| 1.4 | **Restore planner dry-run** | 복구 이미지 마운트 후 계획만 출력, `--apply` 없음 |

**완료 기준:** Phase 1에서 **오류·FAIL CLOSED** 없이 dry-run·읽기 전용 경로가 문서대로 동작.

---

## Phase 2 — 첫 backup 테스트

1. **빈(또는 초기화된) Recovery Image** 준비 (정책에 맞는 디렉터리 구조).
2. **첫 backup** 실행: **dry-run → `--apply --confirm`** 순서 (상세: `docs/FIRST_BACKUP_TEST.md`).
3. **manifest** 생성 및 **SHA256** 사이드카 확인.
4. **backup validation** (불완전 마커 없음, 필수 아티팩트 존재).

**완료 기준:** `incomplete_backup` 없음, 메뉴·상태가 **valid backup** 쪽으로 전환되는지 확인.

---

## Phase 3 — restore dry-run

1. **Planned restore** (executor 미호출, 계획·검증만).
2. **Rollback planning** (GPT/EFI 롤백 플랜 존재 여부).
3. **EFI 검증** (bootmgfw 존재·정책 문구 확인; 상세 rollback 문서 참고).

**완료 기준:** `validate_restore` **PASS**, restore 메뉴가 정책상 허용되는지 확인 (**FAIL이면 Phase 4 금지**).

---

## Phase 4 — 첫 restore 테스트 (소규모)

- **작은 테스트 파일**로만 시작 (OS 손상·전체 패티션 테스트 금지).
- 절차: `docs/FIRST_RESTORE_TEST.md` 준수.
- 재부팅 후 **파일 복구 확인** 및 **rollback 플래그** 미설정 확인.

---

## Phase 5 — 실패 주입·회복 검증

다음 시나리오를 **순차적이고 통제된 환경**에서만 수행 (`docs/ROLLBACK_TEST_PLAN.md`).

| # | 주제 |
|---|------|
| 5.1 | interrupted restore |
| 5.2 | hash mismatch |
| 5.3 | manifest corruption |
| 5.4 | BootOrder drift |
| 5.5 | RecoveryBoot entry loss / drift |

각 항목: **예상 동작**, **rollback 동작**, **Windows survivability**, **로그 수집**을 기록 (`tests/manual/test_result_template.md`).

---

## Phase 6 (선택, 최후)**

- **OS·부트 미디어 극단 테스트** 등은 플랜 전부 통과 후, 별도 승인·환경 하에서만 `KNOWN_LIMITATIONS.md`를 재확인.

---

## 교차 참조

- 사전 게이트: `docs/PRE_DESTRUCTIVE_CHECKLIST.md`
- 자동 게이트: `tools/pre_destructive_gate.py`
- 테스트 매트릭스: `tests/manual/manual_test_matrix.md`

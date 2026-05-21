# Destructive 테스트 전 필수 체크리스트 (20단계)

**목적:** 실사용 PC 또는 전용 테스트 PC에서 **첫 번째 파괴적 backup / restore / BootOrder 수정** 전에 반드시 충족해야 하는 안전 조건을 나열합니다.

**정책:** 아래 체크 항목 중 **하나라도 FAIL·미충족·운영자 부인(no-go)** 인 경우 **`--apply`(파괴적 작업) 금지**입니다. 이 문서만으로 테스트를 수행하지 않습니다. 세부 시나리오는 별도 테스트 플랜을 따릅니다.

**연계 문서:** 파괴 테스트 진입 조건 요약은 [`TEST_ENTRY_CRITERIA.md`](TEST_ENTRY_CRITERIA.md), 릴리스 준비도는 [`RELEASE_READINESS.md`](RELEASE_READINESS.md), 최종 게이트 도구는 `tools/final_release_gate.py`·`tools/release_readiness_check.py`를 참고합니다.

---

## 사전 준비 (운영·환경)

- [ ] **테스트 전 전체 데이터 외부 백업 완료** (복구 이미지·스냅샷만으로 불충분할 수 있음)
- [ ] **테스트 전용 머신 또는 VM 사용** 권장 (프로덕션 일상 업무 환경 사용 금지)
- [ ] **BitLocker OFF** (플랫폼에서 확인 불가 시 수동 확인 후 체크)
- [ ] **Secure Boot OFF 권장** (서명 자동화·OEM 변수 미검증 시 디버깅 용이)
- [ ] **단일 인터널 디스크 + 단일 Windows** 환경
- [ ] **RAID 아님**
- [ ] **Dynamic Disk 아님**
- [ ] **Multi-boot 아님** (별도 리눅스/OS 듀얼 부트 미지원 가정과 충돌 시 테스트 중단)

## Recoverix / 디스크 토폴로지

- [ ] **RECOVERY_IMAGE 파티션(및 필요 시 Recovery Linux)** 공간 및 라벨 정상 인식
- [ ] **Recovery Runtime** 진입 가능 (부팅·마운트·메뉴 정상)
- [ ] **Windows 부팅** 정상 확인 (destructive 테스트 직전)

## 자동 검사·건전성

아래 명령은 가능한 한 **destructive 테스트 당일** 동일 버전 플랫폼에서 실행합니다.

```bash
# 저장소 루트에서
PYTHONPATH=. python3 tools/pre_destructive_gate.py --json   # 결과: diagnostics/pre_destructive_gate.json
```

추적용 개별 명령(게이트에 포함되는 항목):

- [ ] **BootOrder 계획 dry-run 및 Windows Agent dry-run 통과 의미 확인** (`tools/run_integration_checks.py --dry-run` 등 참고)
- [ ] **`safety_audit`** 결과 **PASS 또는 PASS_WITH_WARNINGS** (전체 **FAIL** 이면 중단)
- [ ] **`destructive_command_audit.complete === true`** (리포트 JSON 확인)
- [ ] **`validate_restore` PASS** (복구 루트 마운트·매니페스트·디스크 일치 등; **FAIL이면 restore 금지**)
- [ ] **`rollback_required === false`** (`recovery_state.json` 등)
- [ ] **`incomplete_backup` 마커 없음**
- [ ] **Windows Agent**는 정책상 **dry-run/검사 경로만** 사전 통과 확인 (자세한 절차: `docs/MANUAL_TEST_PLAN.md`)
- [ ] **Integration checks** **PASS 또는 PASS_WITH_WARNINGS** (전체 **FAIL** 이면 원인 조사 후 재실행)

## 체크 완료 기록

| 날짜 | 운영자 | 머신 ID | 게이트 결과 | 비고 |
|------|--------|---------|-------------|------|
|      |        |         |             |      |

---

**요약:** 위 항목이 모두 **PASS/확인**된 경우에만 `FIRST_BACKUP_TEST.md` / `FIRST_RESTORE_TEST.md` 등 **문서화된 수동 절차**로 destructive 단계에 진입합니다.

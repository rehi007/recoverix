# 릴리스 준비도 상태 정의

본 문서는 **코드 기능 완결**과 **현장 검증 단계**, **생산 적합성**을 구분해 표현합니다.  
자동 집계는 `tools/release_readiness_check.py`·`tools/final_release_gate.py`, 상세 진입 조건은 [`TEST_ENTRY_CRITERIA.md`](TEST_ENTRY_CRITERIA.md), 흐름은 [`FINAL_VALIDATION_FLOW.md`](FINAL_VALIDATION_FLOW.md)를 따릅니다.

## 상태 요약

| 상태 | 의미 |
|------|------|
| **NOT_READY** | 기능/감사/번들 검증 미충족. 파괴적 테스트 금지. |
| **READY_FOR_MANUAL_TEST** | 저장소 정적 검증과 드라이런이 충족. **테스트 전용 머신**에서 최초 수동 파괴 테스트 가능 후보(OEM 배포 비아). |
| **READY_FOR_INTERNAL_QA** | 수동 백업/복구/롤백·재부팅 시나리오까지 완료. |
| **READY_FOR_LIMITED_DEPLOYMENT** | 다수 머신·일부 OEM firmware 검증까지 완료. |
| **NOT_SUPPORTED_FOR_PRODUCTION** | 정책/기술 한계로 일반 생산 배포 부적격(별도 상태; [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md)·[`UNSUPPORTED_ENVIRONMENTS.md`](UNSUPPORTED_ENVIRONMENTS.md) 참조). |

## 상태별 진입 요건(요약)

### NOT_READY

- 안전 감사 **FAIL**, 또는 통합 검사 **FAIL**, 또는 **`destructive_command_audit` 미완성·FAIL**
- 필수 문서·EFI 자산·런타임 진입 파일 누락
- 출하 번들 `validate_release` **FAIL** (해당 검사 수행 시)
- 플레이스홀더 토큰이 생산 코드 경로에 잔류

### READY_FOR_MANUAL_TEST

- `safety_audit` **PASS 또는 PASS_WITH_WARNINGS**
- `integration checks` **PASS 또는 PASS_WITH_WARNINGS**
- `destructive_command_audit` **완료(complete)·상태 허용**
- 수동 절차 문서 및 [`PRE_DESTRUCTIVE_CHECKLIST.md`](PRE_DESTRUCTIVE_CHECKLIST.md) 준비 완료
- (권장) `release/dist` 구축 후 `validate_release` **PASS**
- 테스트 대상 호스트에서는 `pre_destructive_gate` **PASS**(또는 경고 허용) — 자동 결과는 진단 JSON에 포함

### READY_FOR_INTERNAL_QA

- **수동 파괴 테스트** 및 [`MANUAL_TEST_PLAN.md`](MANUAL_TEST_PLAN.md)의 롤백·재부팅 계열 시나리오 완료
- 장애·로그 회수 후 판단 기록 유지 (`tests/manual/test_result_template.md`)

### READY_FOR_LIMITED_DEPLOYMENT

- 복수 하드웨어· BIOS/UEFI 버전에서 반복 검증
- 가능한 범위의 **OEM firmware** 조합 검증

### NOT_SUPPORTED_FOR_PRODUCTION

- Secure Boot 서명 자동화 미완·벤더 커버리지 부족·런타임 이식성 제한 등 **정책상 생산 출하 불가**를 프로젝트가 명시적으로 선언하는 경우

## 자동 판정과의 관계

- `release_readiness_check` 출력의 **`readiness`**: `READY` / `READY_WITH_WARNINGS` / `NOT_READY`
- 동일 JSON의 **`status`**(또는 `release_stage_recommendation`)는 위 워크플로 단계 중 **자동으로 유도 가능한 최대치**(일반적으로 `READY_FOR_MANUAL_TEST` 또는 `NOT_READY`)를 나타냅니다.
- **`PASS`·`READY`는 생산 준비를 의미하지 않습니다.** 최대 목표는 [TEST_ENTRY_CRITERIA.md](TEST_ENTRY_CRITERIA.md)에 따른 **안전한 첫 파괴적 테스트 진입**까지입니다.

## 관련 문서

- [`TEST_ENTRY_CRITERIA.md`](TEST_ENTRY_CRITERIA.md) — 파괴적 테스트 시작 조건
- [`FINAL_VALIDATION_FLOW.md`](FINAL_VALIDATION_FLOW.md) — 단계·차단·롤백 포인트
- [`PRE_DESTRUCTIVE_CHECKLIST.md`](PRE_DESTRUCTIVE_CHECKLIST.md) — 운영자 체크리스트
- [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) — 알려진 한계

# 최종 검증 흐름 (제품 검증 워크플로)

파괴적 연산 없이 진행 가능한 단계와, **반드시 전용 테스트 머신**에서만 진행하는 단계를 구분합니다.

## 전체 도식

```text
Developer Build
        │
        ▼
   Safety Audit
        │
        ▼
Integration Checks  (항상 dry-run / 읽기 전용)
        │
        ▼
Pre-destructive Gate   (테스트 호스트에서는 Runtime 컨텍스트 권장)
        │
        ▼
Manual Backup Test    [전용 머신 · PRE_DESTRUCTIVE_CHECKLIST 충족]
        │
        ▼
Manual Restore Test   (초기에는 작은 테스트 파일만)
        │
        ▼
Rollback Test         (중단·해시 불일치·manifest·BootOrder 등)
        │
        ▼
EFI Recovery Test     (복구 프로시저 검증 · [`EFI_RECOVERY_PLAN.md`](EFI_RECOVERY_PLAN.md))
        │
        ▼
Internal QA           (복수 머신·로그·회귀)
        │
        ▼
Limited Deployment  (좁은 채널 파일럿 · OEM별 조건)


```

## 단계별 표

| 단계 | Required status | Blocking conditions | Rollback point |
|------|----------------|---------------------|----------------|
| Developer Build | 산출물 일관성 | 빌드 실패·필수 모듈 누락 | 이전 커밋/태그로 복귀 |
| Safety Audit | **PASS 또는 PASS_WITH_WARNINGS** | **FAIL** (`destructive` 미완·정책 위반 포함) | 위험 코드 병합 중단 |
| Integration Checks | **PASS 또는 PASS_WITH_WARNINGS** | **FAIL** (하드코딩 등 FAIL CLOSED 정책) | 패치 회수 |
| Pre-destructive Gate | **PASS**(또는 경고 허용 시 **PASS_WITH_WARNINGS**) | **FAIL**(BitLocker·검증·부트 매니저·RecoveryBoot 등) | Runtime/Windows 부팅으로 복귀 가능 시 확인 후 재시도 |
| Manual Backup Test | 작은 테스트 파일 패턴 준비 | 체크리스트 미달·백업 없음 | 테스트 머신만 영향 |
| Manual Restore Test | 백업·검증 완료 | **OS 손상 시나리오 금지**(첫 라운드) | Rollback 플랜 준비 |
| Rollback Test | 실패 삽입 시나리오 합의 | 무단 생산 실행 | 에이전트/Runtime 문서별 복귀 절차 |
| EFI Recovery Test | 수동 복구 방법 숙지 | 무작정 destructive restore 선행 금지 | [`EMERGENCY_BOOT_RECOVERY.md`](EMERGENCY_BOOT_RECOVERY.md) |
| Internal QA | 문서화된 결과·로그 | 심각 이슈 미해결 | 출하 보류 |
| Limited Deployment | 제한 채널 승인 | 범위 밖 장비 배포 | 파일럿 중단·롤백 |

## 자동 진단 산출물

| 파일 | 역할 |
|------|------|
| `diagnostics/release_readiness.json` | 저장소 종합 READY 판별 |
| `diagnostics/pre_destructive_gate.json` | 테스트 직전 호스트 게이트 |
| `diagnostics/final_release_gate.json` | 패키징/최초 현장 테스트 전 최종 승인 |

## 원칙

- **`PASS ≠ production ready`**, **`READY_FOR_MANUAL_TEST ≠ OEM 생산 허가`**
- Windows 부팅 생존성·롤백 가능성·운영자 인지 우선 (`RELEASE_READINESS.md` 참조)

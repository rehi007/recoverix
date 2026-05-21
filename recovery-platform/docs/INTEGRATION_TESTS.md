# 통합 테스트(Integration checks)

## 목적

통합 레이어는 **실제 backup/restore apply를 실행하지 않고**, 전체 플랫폼의 **플래너·읽기 전용 검사·에이전트 dry-run**을 한 번에 점검합니다.

## 자동 실행

```bash
cd recovery-platform
PYTHONPATH=. python3 tools/run_integration_checks.py --dry-run
PYTHONPATH=. python3 tools/run_integration_checks.py --dry-run --json
PYTHONPATH=. python3 tools/collect_diagnostics.py --output diagnostics/diagnostics.json
```

- 결과: `diagnostics/integration_report.json`
- **`--dry-run`은 필수**이며 통합 스크립트에는 **`--apply` 옵션이 없습니다.**

## 결과 구조

각 체크는 **`PASS`** / **`FAIL`** / **`SKIPPED`** 중 하나입니다. 예외 발생 시 해당 체크는 **`FAIL`(fail_closed)** 처리되며 다음 체크를 계속합니다.

전체 상태는 **`PASS`** / **`PASS_WITH_WARNINGS`** / **`FAIL`**(하드코딩 감사 등 치명 실패 시)로 요약됩니다.

## 수동 destructive 테스트 분리 정책

> **⚠️ 중요**  
> **실제 디스크 쓰기·복구 적용 테스트**는 저장소 표준 통합 점검에 포함하지 **않습니다.**  
> 별도 **테스트 전용 PC/VM**과 **운영 매뉴얼 destructive 절차**에서만 수행하십시오.

테스트 시나리오 참고: `tests/integration/test_scenarios.md`

## 진단 마스킹

`collect_diagnostics`는 GUID·장문 문자열 등을 줄여 개인식별·대량 노출을 완화합니다.

## 테스트 코드

`tests/integration/test_integration_safety.py` — 러너 안전 속성 검증(`unittest`).

## 제품 번들

통합 검사 외 **`scripts/build_release.py`** / **`validate_release.py`** / **`package_release.py`** 로 `release/dist/` 형태 번들 생성·패키지 압축 가능합니다(`release/README.md` 참조).

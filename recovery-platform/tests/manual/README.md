# Manual destructive tests (`tests/manual/`)

## 목적

이 디렉터리는 **`pytest`/CI 자동 테스트가 아닌**, **운영자가 통제 하에 수행하는** 파괴적 backup/restore·rollback·EFI 관련 검증을 위한 **문서·매트릭스**를 둡니다.

## 정책 (필독)

1. **Manual destructive tests only** — 문서에 나온 시나리오만, **승인된 테스트 PC / VM**에서 수행합니다.
2. **Production machine 금지** — 일상 업무용 PC에서의 첫 `--apply` 금지.
3. **전체 데이터 백업 필수** — Recoverix 백업만으로는 충분하지 않을 수 있습니다.
4. **VM 권장** — 스냅샷·롤백이 쉬운 환경을 권장합니다.
5. **첫 restore는 작은 테스트 파일만** — OS 손상·전체 디스크 복구 테스트는 금지.
6. **OS corruption 테스트는 플랜의 마지막 단계** — 전 단계 및 게이트 **PASS** 후 별도 승인 필요.

## 관련 파일

| 파일 | 역할 |
|------|------|
| `manual_test_matrix.md` | 시나리오 ID와 기대 결과 표 |
| `test_result_template.md` | 테스트 기록 작성 양식 |
| `docs/PRE_DESTRUCTIVE_CHECKLIST.md` | 사전 필수 확인 |
| `docs/MANUAL_TEST_PLAN.md` | 위계적 플랜 |
| `tools/pre_destructive_gate.py` | 자동 사전 게이트 (Linux Recovery Runtime 권장) |

## 자동 게이트

파괴적 테스트 직전:

```bash
PYTHONPATH=. python3 tools/pre_destructive_gate.py
```

`FAIL`이면 **어떤 `--apply`도 실행하지 않습니다.**

# Recovery Runtime (Linux)

## 개요

Recovery Runtime은 **Linux 전용** 복구 세션에서 동작하는 에이전트입니다. Windows에서 동일 바이너리를 실행하지 않습니다.

## 구조

| 구성 요소 | 역할 |
|-----------|------|
| `main.py` | 부트스트랩·비대화형 상태 출력 |
| `runtime_context.py` | 디스커버리·검증 결과·메뉴 가용성 집계 |
| `menu.py` | TUI 루프(키보드 전용) |
| `actions.py` | 백업·복구·삭제·로그·상태 액션 래퍼(try/except·error.log) |

## TUI 메뉴 정책

- **메뉴 보임(visible) ≠ 실행 가능(executable)**  
  검증 실패·BitLocker·토폴로지 이유로 비활성화해도 항목은 표시하고 **사유**를 보여줍니다.

## 검증·복구

- `validate_restore`, `apply_persisted_recovery_state` 등 상위 모듈과 연동됩니다.
- Destructive 동작 전 **최종 확인**·**phrase** 등 정책을 따릅니다.

## immutable 런타임

OS 루트 이미지는 **읽기 중심(immutable)** 배포를 목표로 하고, 가변 데이터는 **RECOVERY_IMAGE** 쪽에만 기록합니다 ([`README.md`](README.md)).

이미지 빌드 파이프라인(squashfs + overlay initramfs, **호스트 부트 미변경**): [`IMMUTABLE_RUNTIME_BUILD.md`](IMMUTABLE_RUNTIME_BUILD.md) · `scripts/runtime_image/`

## 관련 문서

[`RESTORE.md`](RESTORE.md) · [`FAILURE_RECOVERY.md`](FAILURE_RECOVERY.md) · [`BACKUP.md`](BACKUP.md)

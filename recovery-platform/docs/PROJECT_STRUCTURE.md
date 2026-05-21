# 프로젝트 구조 (`recovery-platform/`)

상업 배포 번들에는 `scripts/build_release.py`가 `product_manifest.json`을 기준으로 하위 디렉터리·문서 일부만 복사합니다. 여기서는 **개발 저장소 최상단** 구조를 기준으로 설명합니다.

## 디렉터리 트리(요약)

```text
recovery-platform/
├── backup_engine/        Windows/EFI 영역 이미지 백업 플래닝·실행(partclone)·매니페스트
├── restore_engine/       검증 게이트·복원 실행·플래너(파괴적 경로 포함)
├── rollback/             EFI/GPT 롤백·실패 카운트 등 복구 실패 후퇴
├── recovery_runtime/     Linux 전용 Recovery TUI·마운트·디스커버리
├── windows_agent/        Windows 측 bcd/BootOrder 모니터링·repair(직접 Linux 파티션 쓰기 없음)
├── boot_manager/         UEFI 진입 shim/grub 레이아웃·BootOrder 플래닝 등
├── partition_manager/    동적 디스크 발견·프로비저닝 계획
├── grub/                 GRUB 생성 보조 로직
├── validation/           이미지/파티션/시스템 형식 검증
├── common/               공용 설정·커맨드 래퍼·로깅 지원 등
├── config/               예: `product_manifest.json`(번들 포함 목록·버전 메타)
├── docs/                  운영·정책·수동 테스트 절차 문서
├── tests/                  단위·통합(비파괴)·릴리스 게이트 테스트 등
├── scripts/              빌드·패키지·출하 번들 `validate_release` 스크립트
└── tools/                감사·통합 검사·게이트·진단 도구(자동화 엔트리)
```

`release/`(빌드 산출)은 로컬·CI 생성물이므로 버전 관리에서는 보통 소스만 문서화합니다.

## 책임 분리 개요

| 구역 | 주요 책임 |
|------|-----------|
| **Windows-side** | `windows_agent/` — 사용자 세션/OS 내에서 허용된 API만 사용, 복구 런타임과의 공존 중 BootOrder 안정화. |
| **Linux-side** | `recovery_runtime/`과 대부분의 `backup_engine/`·`restore_engine/` 실행 경로, `rollback/`에서 디스크/EFI 조작 가능. |
| **Destructive modules** | `restore_engine/`(복원 apply), EFI 쓰기 경로를 포함할 수 있는 `rollback/` 등 — `destructive_command_audit`로 별도 감사. |
| **Validation modules** | `validation/` 및 플래너단 FAIL CLOSED 검사. |
| **Audit modules** | `tools/safety_audit.py`, `destructive_command_audit.py`, `run_integration_checks.py`, `release_readiness_check.py`, `final_release_gate.py` 등. |

## 관련 명령

- 디렉터리·파일 개수 요약: `PYTHONPATH=. python3 tools/project_tree_report.py [--json]`
- 릴리스 준비도: [`RELEASE_READINESS.md`](RELEASE_READINESS.md) 및 `tools/release_readiness_check.py`
- 종합 검증 플로우: [`FINAL_VALIDATION_FLOW.md`](FINAL_VALIDATION_FLOW.md)

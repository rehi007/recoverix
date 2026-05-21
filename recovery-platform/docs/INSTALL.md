# 설치 가이드 (Product installation)

## 경고

- 설치·프로비저닝·EFI 수정은 **데이터 소실 가능성**이 있습니다. **먼저 전체 디스크 백업**(이미지·중요 파일)을 권장합니다.
- **테스트 전용 PC/VM**으로 시나리오 검증 후 프로덕션에 적용하십시오.
- **`--apply` 또는 실제 디스크 쓰기**가 수반되는 명령은 항상 **dry-run**(또는 설계 문서된 사전 검사)부터 수행하십시오.

---

## 소프트웨어·플랫폼 요구사항

| 항목 | 요구 |
|------|------|
| Windows | Windows 10 / 11 (지원 채널 기준 버전 고정 안내 필요 시 OEM 정책에 따름) |
| 펌웨어 | **UEFI** (**Legacy BIOS 비지원** — [`UNSUPPORTED_ENVIRONMENTS.md`](UNSUPPORTED_ENVIRONMENTS.md)) |
| 파티션 | **GPT** (**MBR 비지원**) |
| Secure Boot | **선택** — 구현 가능 범위·서명 검증 정책은 [`SECURE_BOOT.md`](SECURE_BOOT.md) 참고 |
| BitLocker | **실행 채널에서 OFF 필수**(백업/복구/EFI/BootOrder repair 경로 차단 — [`BITLOCKER_POLICY.md`](BITLOCKER_POLICY.md)) |
| 디스크 토폴로지 | **단일 Windows 부팅 디스크**(다중 OS/다중 부팅 디스크 비지원) |

---

## 설치 단계 개요

1. **preflight** — 관리자 권한, UEFI/GPT 확인, BitLocker OFF 등 — Windows 측 `preflight`/문서 확인
2. **partition planning** — EFI/MSR/Windows/`RECOVERY_IMAGE`/`RECOVERY_LINUX` 레이블·크기 규격 — [`PARTITION_POLICY.md`](PARTITION_POLICY.md)
3. **Recovery partition provisioning** — 정책에 맞게 파티션 생성·포맷(도구/OS별 명령은 OEM 패키지에 따름). **번호 하드코딩 금지**
4. **EFI deployment** — RecoveryBoot 에셋(shim/grub/cfg 규격) 배치 경로 검증 — `bootmgfw.efi` **덮어쓰기 금지**
5. **RecoveryBoot registration** — 펌웨어 목록 추가·표시 순서 정책 — [`RECOVERYBOOT_POLICY.md`](RECOVERYBOOT_POLICY.md)
6. **initial backup** — Recovery Runtime 또는 문서된 절차로 **첫 유효 백업**(manifest·HASH·completion 마커 포함)
7. **validation** — `validate_restore`·무결성·디바이스 ID 일치 등 — 실패 시 **FAIL CLOSED** ([`FAILURE_RECOVERY.md`](FAILURE_RECOVERY.md))

각 단계는 문서 또는 스크립트에 **dry-run(또는 시뮬레이션) 모드가 있으면 선행 실행**해야 합니다.

---

## 검증 순서 제안

- Windows: 분할 디스커버리/`bcdedit`/preflight류 **readonly** 명령
- Linux Recovery: 플래너(`backup_planner`/`restore_planner`) **항상 `--dry-run` 전제**
- Windows Agent: **`fix_bootorder_task`** 는 기본 dry-run (repair는 명시 `--apply`)

---

## 패키지·배포

- 빌드: `PYTHONPATH=. python3 scripts/build_release.py` → `release/dist/` 생성
- 검증: `python3 scripts/validate_release.py --root release/dist` (**필수 산출 누락 시 FAIL CLOSED**)
- GOLD 출하 전: **`--strict`** 플레이스홀더·`-dev` 버전 제거 후 재검증
- 패키징: `python3 scripts/package_release.py`

자세히는 저장소 **`release/README.md`**, **`config/product_manifest.json`** 참고합니다.

---

## 지원 책임

실제 디스크에 대한 설치 책임 범위·SLA는 **OEM 라이선스·서비스 계약**에 따라 별도 정의합니다. 본 문서는 플랫폼 역할 분리 및 안전 정책을 설명하기 위함입니다.

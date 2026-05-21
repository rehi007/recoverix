# RecoveryBoot 복구 플랫폼 — 제품 개요

상업적/OEM 배포를 전제로 한 RecoveryBoot 기반 재해 복구·백업·복구 플랫폼입니다. 본 저장소는 **Windows 부팅 경로 우선**(Windows Boot Manager·`bootmgfw.efi` 보호) 원칙을 최우선으로 합니다.

## 핵심 기능

- **RecoveryBoot**: UEFI 표준 부트 체인에서 복구 Linux(Recovery Runtime)로 진입
- **Backup Engine**: partclone.ntfs 등을 이용한 Windows/EFI 이미지 백업(정책·검증 포함)
- **Restore Engine**: 검증 후 복구(dry-run/phrase/confirm 게이트, destructive 명시)
- **Rollback Engine**: EFI/GPT 및 실패 카운터·BootOrder 정책 연계
- **Recovery Runtime**: Linux 전용 ncurses/TUI 및 메뉴 기반 안전 실행
- **Windows Agent**: EFI/NVRAM 모니터링·BootOrder repair(확장/리눅스 파일시스템 미접근)
- **Integration Safety Tests**: 통합 드라이런 및 진단 수집(실 destructive 테스트와 분리)

## RecoveryBoot 구조(요약)

- ESP에 배치되는 **RecoveryBoot**(shim/grub/recovery 진입점)와
- 디스크 상 **RECOVERY_IMAGE**(백업·메타·로그)·**RECOVERY_LINUX**(복구용 Linux) 역할 분리를 전제로 합니다.  
파티션 **번호 고정**(예: 1번=EFI) 또는 **Boot0000** 같은 슬롯 하드코딩은 허용되지 않습니다. 항상 **동적 디스커버리**를 사용합니다.

## immutable Recovery Runtime

Recovery Runtime 이미지는 **사용 중 변경을 최소화**하는 롤링/읽기 전용 설계가 원칙입니다. 백업·상태 파일·로그 등 **가변 데이터는 RECOVERY_IMAGE**(및 설계된 state 경로)에만 기록합니다. 런타임 OS 루트를 임의로 쓰기 가능 상태로 두는 배포는 지원 목적에 맞지 않습니다.

## 지원 환경

자세히는 [`UNSUPPORTED_ENVIRONMENTS.md`](UNSUPPORTED_ENVIRONMENTS.md)·[`INSTALL.md`](INSTALL.md)를 참고합니다.

**지원 대상의 요약**

- Windows 10/11, **UEFI**, **GPT**
- **단일 Windows 부팅 디스크**(정책상 다중 디스크/다중 OS 비지원)
- **BitLocker OFF**(백업/복구/EFI 쓰기·BootOrder repair 차단 가능)
- Recovery Linux 분할·복구 분할 레이블/마운트가 설계 규격에 맞게 구성됨

## 비지원 환경

Legacy BIOS/MBR·RAID·동적 디스크·**BitLocker ON** 등은 **비지원** 또는 정책상 제한됩니다. 해당 목록은 [`UNSUPPORTED_ENVIRONMENTS.md`](UNSUPPORTED_ENVIRONMENTS.md).

## 고수준 아키텍처

```text
[ Windows / OEM 설치 미디어 검증 단계 생략 ]
        │
        ▼
   UEFI Firmware
        │
        ├─► BootOrder 표시 순서 상 RecoveryBoot 진입 또는 Windows Boot Manager
        │
RecoveryBoot(shim)→GRUB→(timeout/기본) Windows 체인로드 ──► Windows Boot Manager ──► Windows
        │
        └─ F5 / 별도 진입 ──► Recovery Runtime (Linux) ──► TUI (백업/복구/검증/롤백 상태)
Windows Agent ──► bcdedit/펌웨어 열람 ──► BootOrder drift 검출·repair(정책·BitLocker·권한)
```

## 안전 정책(FAIL CLOSED)

- 검증 실패 시 **복구 실행 비활성화**(표시≠실행 허용)
- **Destructive 테스트는 저장소 통합 테스트에 포함하지 않음** → 별도 **manual 전용 테스트 머신**
- **`bootmgfw.efi` 정책상 덮어쓰기 금지**·Windows 부팅 생존성 우선

## 설치 흐름(요약)

[`INSTALL.md`](INSTALL.md): preflight → 파티션 기획 → Recovery 프로비저닝 → EFI 배포 → RecoveryBoot 등록 → **초기 백업** → 검증(드라이런 우선).

## 백업 / 복구 / Rollback 개요

- [`BACKUP.md`](BACKUP.md) · [`RESTORE.md`](RESTORE.md) · [`ROLLBACK.md`](ROLLBACK.md)

## 표준 부팅 흐름(ASCII)

```text
UEFI
 → RecoveryBoot (기본 선택 또는 최우선; timeout 후 Windows 허용)
     → shim (보안 부팅 호환 선택 시)
       → GRUB
         → 기본 선택: chainload 또는 Windows 부트 경로 진입 규격
 → Windows Boot Manager (\EFI\Microsoft\Boot\bootmgfw.efi)
 → Windows 로더
```

### F5(또는 정책상 복구 진입키)

```text
F5 (또는 OEM 정의 진입키)
 → RecoveryBoot GRUB 내 recovery 진입 레이블
   → Recovery Linux
     → Recovery Runtime (TUI)
```

## 배포·릴리스 번들

- **`release/README.md`** — 빌드 산출 디렉터리(`release/dist/`) 레이아웃
- **`config/product_manifest.json`** — 포함 패키지·필수 문서 목록·버전
- **`scripts/build_release.py`** / **`validate_release.py`** / **`package_release.py`** — 번들 빌드·FAIL CLOSED 검증·zip/tar.gz 패키징
- GOLD 출하 전에는 **`validate_release.py --strict`** 로 개발 버전·플레이스홀더 잔류를 차단해야 합니다.

## 더 읽기

| 문서 | 내용 |
|------|------|
| [INSTALL.md](INSTALL.md) | 요구사항·설치 단계 |
| [RECOVERYBOOT_POLICY.md](RECOVERYBOOT_POLICY.md) | BootOrder·타임아웃·생존성 |
| [PARTITION_POLICY.md](PARTITION_POLICY.md) | 파티션 역할·동적 디스커버리 |
| [BACKUP.md](BACKUP.md) / [RESTORE.md](RESTORE.md) | 백업·복구 세부 |
| [ROLLBACK.md](ROLLBACK.md) / [FAILURE_RECOVERY.md](FAILURE_RECOVERY.md) | 실패·롤백·루프 방지 |
| [BITLOCKER_POLICY.md](BITLOCKER_POLICY.md) | BitLocker 차단 근거 |
| [SECURE_BOOT.md](SECURE_BOOT.md) | Secure Boot 체인·구현 한계 |
| [WINDOWS_AGENT.md](WINDOWS_AGENT.md) · [RECOVERY_RUNTIME.md](RECOVERY_RUNTIME.md) | Windows/Linux 런타임 분리 |
| [LOGGING_POLICY.md](LOGGING_POLICY.md) | 로그 위치·로테이션 |
| [INTEGRATION_TESTS.md](INTEGRATION_TESTS.md) | 드라이런 통합 점검 |
| [SAFETY_AUDIT.md](SAFETY_AUDIT.md) | 정적 안전·파괴 명령 감사 |
| [RELEASE_READINESS.md](RELEASE_READINESS.md) | 릴리스 준비도 상태 정의 |
| [TEST_ENTRY_CRITERIA.md](TEST_ENTRY_CRITERIA.md) | 파괴적 테스트 시작 조건 |
| [FINAL_VALIDATION_FLOW.md](FINAL_VALIDATION_FLOW.md) | 최종 검증·QA·배포 흐름 |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | 저장소 디렉터리·책임 분리 |
| [PRE_DESTRUCTIVE_CHECKLIST.md](PRE_DESTRUCTIVE_CHECKLIST.md) / [MANUAL_TEST_PLAN.md](MANUAL_TEST_PLAN.md) | 수동 테스트 전 체크·플랜 |
| [UNSUPPORTED_ENVIRONMENTS.md](UNSUPPORTED_ENVIRONMENTS.md) · [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) | 비지원·알려진 한계 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | 장애 대응 |
| (저장소) `release/README.md` | 번들 디렉터리 구조 |

**게이트 CLI(파괴 없음):** `tools/release_readiness_check.py` · `tools/final_release_gate.py` · `tools/pre_destructive_gate.py`

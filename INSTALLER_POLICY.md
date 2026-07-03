# Recoverix Installer Policy

## Supported Environment

설치 대상:

- Windows 64-bit
- UEFI boot
- GPT system disk
- 관리자 권한
- BitLocker 꺼짐
- EFI System Partition 접근 가능

지원하지 않는 환경:

- 32-bit Windows
- Legacy/CSM 전용 부팅
- MBR 시스템 디스크
- BitLocker 보호 중인 C: 파티션
- C: 파티션 축소 불가 상태

## Partition Layout

설치 프로그램은 Windows C: 파티션 뒤쪽을 축소해 Recoverix 영역을 만듭니다.

생성 대상:

```text
Windows C:
100MB unallocated reserve
RECOVERY_LINUX 4GB
RECOVERY_IMAGE calculated size
```

현재 정책:

```text
RECOVERY_LINUX = 4GB
RECOVERY_IMAGE = max(Windows used space * 0.75 + 10GB, 35GB)
Windows tail unallocated reserve = 100MB
```

설치 안내:

```text
Recoverix 설치에는 최소 약 40GB 이상의 확보 가능한 공간이 필요합니다.
실제 사용되는 공간은 Windows 사용량에 따라 자동 계산됩니다.
복구/백업 공간은 Windows 탐색기에서 일반 드라이브처럼 보이지 않을 수 있습니다.
설치 후 C: 드라이브 용량이 줄어든 것처럼 보일 수 있습니다.
```

## Backup Space Policy

백업 실행 전 필요 공간 계산:

```text
Windows current used space * 0.75 + 2GiB
```

설치 계산식과 백업 계산식은 의도적으로 다릅니다.

- 설치 계산식은 향후 Windows 사용량 증가와 재백업을 대비해 더 크게 잡습니다.
- 백업 계산식은 실제 백업 성공 가능성에 가깝게 더 낮게 잡습니다.

## Boot Policy

기본 부팅:

- Recoverix Hotkey Boot가 부팅 흐름을 담당합니다.
- 사용자가 `q`/`Q`를 누르지 않으면 Windows로 부팅합니다.
- `q`/`Q`를 누르면 Recoverix GUI로 진입합니다.

F12 부팅 항목:

- Recoverix Hotkey Boot: 핫키 대기 부팅
- Start Recoverix: 복구 모드 직접 진입

Windows Agent:

- Windows 부팅 후 RecoveryBoot NVRAM 항목을 점검합니다.
- 항목이 사라졌으면 재등록합니다.
- 정상 등록되어 있으면 별도 동작하지 않습니다.

## Uninstall Policy

제거 시 Windows 구성요소는 제거합니다.

유지 대상:

- `RECOVERY_LINUX`
- `RECOVERY_IMAGE`
- 기존 백업 이미지

이유:

- 사용자가 프로그램 제거를 눌렀다고 기존 복구 이미지까지 삭제하면 데이터 복구 기회를 잃을 수 있습니다.
- 복구 파티션과 백업 삭제는 별도 관리자 기능 또는 수동 절차로 처리합니다.

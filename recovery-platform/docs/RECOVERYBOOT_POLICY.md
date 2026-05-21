# RecoveryBoot 정책

## 목적

RecoveryBoot는 **평소에는 Windows 부팅이 가능해야 하고**, 필요할 때만 **복구용 Linux(Recovery Runtime)** 로 안전하게 진입할 수 있도록 설계된 UEFI 부트 레이블·체인입니다.

## 기본 체인(개념)

- RecoveryBoot 항목이 먼저 보이거나 짧게 선택되도록 할 수 있으나 **타임아웃 후 Windows** 선택이 가능해야 하는 구성을 권장합니다.
- **Boot0000 같은 펌웨어 슬롯 번호 문자열에는 의존하지 않습니다.** 식별은 **EFI 파일 경로**와 **설명 문자열**(제조 규격)을 기준으로 합니다.
  - RecoveryBoot shim 예: `\EFI\RecoveryBoot\shimx64.efi`
  - Windows Boot Manager 예: `\EFI\Microsoft\Boot\bootmgfw.efi`

## F5 또는 OEM 정의 복구 진입

GRUB 메뉴에서 recovery 항목을 통해 **복구 리눅스 커널**로 부팅한 뒤 **Recovery Runtime TUI**에 진입합니다.

## BootOrder 안정화 (Windows Agent)

- Windows 업데이트·BIOS/펌웨어 업데이트 후 **BootOrder 변동**(drift)을 검출합니다.
- **dry-run에서는 수정 없음**; repair는 정책·BitLocker·권한이 충족될 때만 적용 가능합니다(`windows_agent/fix_bootorder_task`).

## Windows-first 생존성

- 장애·반복 부팅 실패 시에는 **Windows로 돌아갈 여지 없이 무한 재시도**하지 않습니다.
- 참조되는 **RecoveryBoot 진입 후 실패 횟수** 임계(예: 3회) 이후에는 **Windows 우선** 같은 정책으로 전환될 수 있습니다(구현·상태파일 참조).

## 자동 무한 재시도 금지

복구·롤백 상태 전이에서는 **카운터·상태 JSON** 등으로 무한 재시도 루프를 막습니다.

## BootNext 및 펌웨어 제약

BootNext 처리는 상태 보존·검증을 함께 문서화합니다. 구체 필드 의미는 `recovery_state` 스키마를 따르십시오.

## 관련 문서

[`WINDOWS_AGENT.md`](WINDOWS_AGENT.md) · [`FAILURE_RECOVERY.md`](FAILURE_RECOVERY.md) · [`README.md`](README.md)

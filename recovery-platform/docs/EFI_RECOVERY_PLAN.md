# EFI 수동 복구 계획 (EFI Recovery)

**원칙 (최우선):** **Windows 부트 가능성 및 bootmgfw 등 Windows Boot Manager 무결성**을 최우선으로 한다. RecoveryBoot 진입 불가 상태에서도 Windows로 복귀 가능한 경로를 먼저 확보한다.

**이 문서는 자동 실행이 아니라 운영자 수동 조치 안내입니다.**

---

## 사전 확인

1. 해당 PC에서 **복구 진행 즉시 중단** 가능한 상태인지 (추가 쓰기 최소화).
2. 불가하면 **외부 저장소와 별도 Windows 설치 미디어** 준비.

---

## Windows Boot Manager 복구

1. 가능하면 **내장 재생 도구**(Windows 시작 복구)보다 **명시적 디스크/EFI 확인** 우선으로 상황을 구분합니다.
2. **bootmgfw.efi**(일반적으로 `\EFI\Microsoft\Boot\bootmgfw.efi`)가 ESP에 존재하는지 **읽기 전용**으로 확인.

## ESP 마운트 (Linux 또는 WinPE 계열)

**Linux:**

- 디스커버리 결과의 **EFI System Partition**(vfat)·장치 경로를 확인.
- `mount -t vfat /dev/<esp> /mnt/esp -o rw` 또는 환경에 맞는 **읽기 전용 먼저** 마운트 시도 가능.

**Windows:**

- 관리자 권한 `diskmgmt.msc`로 ESP 문자 할당 또는 `mountvol` 활용 가능 여부 검토.

운영 규칙: **복구 과정 중 bootmgfw.efi를 무조건 다른 파일로 교체하지 않는다**(정상 복구 절차·백업이 있을 때만).

---

## bcdedit (Windows 환경)

- **Firmware 열거**(읽기 전용): `bcdedit /enum firmware` 결과를 저장 후 분석합니다.
- **BootOrder 수정**은 Recovery 플래너 또는 Windows Agent 계획과 **충돌하지 않도록** 신중하게 수행합니다.
- **BootNext로 Recovery를 강제하는 변경은 정책상 금지**에 해당할 수 있습니다 (프로덕트 정책과 운영자 가이드를 동시 참고).

## bootrec

- 필요 시 **`bootrec /scanos`**, **`bootrec /fixmbr`**, **`bootrec /fixboot`**, **`bootrec /rebuildbcd`** 순을 검토합니다.
- UEFI GPT 환경에서의 부작용(잘못된 BCD) 가능성 때문에 **항상 변경 전 상태 백업**(스크린샷·bcdexport 등).

---

## RecoveryBoot 조건부 제거 또는 비활성화

정책·라이선스에 위배되지 않고 **Windows 우선 부팅**이 필요하면:

1. 펌웨어 BootOrder에서 RecoveryBoot 입력을 우선 순위 후순 또는 비활성 (OEM별 UI).
2. ESP에서 RecoveryBoot 디렉터리를 다룰 경우 **항상 변경 전 디렉터리 복사**와 복귀 방법 확보 후 수행합니다.

프로덕트는 **RecoveryBoot 제거 후에도 Windows Boot Manager 존재**를 전제합니다.

---

## Windows-first fallback

1. 펌웨어에서 **Windows Boot Manager** 직접 선택.
2. 실패하면 **설치 미디어**로 부팅하여 **복구 명령 프롬프트** 시작.
3. RecoveryBoot 또는 파괴적 restore 재시행은 **별도 검증**(게이트 **PASS**) 후 제한적으로만.

---

**참조:** 긴급 부팅 루틴은 `docs/EMERGENCY_BOOT_RECOVERY.md`를 따르고, 극단 시 본 문서 세부 명령을 조합합니다.

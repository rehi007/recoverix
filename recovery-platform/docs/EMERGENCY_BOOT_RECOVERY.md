# 응급 부팅 복구 (Emergency Boot Recovery)

**용도:** 복구·restore 시도 후 **부팅이 되지 않을 때**의 **응급 순서**입니다.

---

## 원칙 (반드시)

1. **`restore --apply`(파괴적 복구)를 응급 첫 번째 행동으로 하지 않는다.**
2. **Windows 살리기**(Windows Boot Manager, ESP 가용성)**를 먼저** 시도합니다.
3. 한 단계 수정 후 재부팅·로그 확인; 무리한 반복 수정 금지.

---

## 권장 순서

### 1. BIOS/UEFI 펌웨어 진입

- 전원 끔/켬 또는 OEM 지정 키(Fn, Del, Esc 등).

### 2. Windows Boot Manager 선택

- 부팅 항목에 있으면 **Windows Boot Manager** 직선 선택 시도.

### 3. RecoveryBoot 비활성·후순

- 테스트나 복귀 위해 임시로 RecoveryBoot 선택을 회피 (BootOrder 순서 조정 또는 일시적으로 비표시).

### 4. EFI 수동 점검

- 다른 OS에서 ESP를 **읽기 전용 마운트** 후 `EFI/Microsoft/Boot/bootmgfw.efi` 존재 확인.
- RecoveryBoot 디렉터리만 존재하고 Windows 항목이 깨져 있으면 **EFI_RECOVERY_PLAN.md** 진행.

### 5. BootOrder reset

- OEM 기본값 복귀 기능이 있다면 활용 후 **반드시 Windows 항목**으로 재설정 검증.

### 6. Windows startup repair / 미디어

- Windows 설치 USB → **복구** → 시작 복구 또는 명령 프롬프트에서 `bcdedit`/diskpart 등 **EFI_RECOVERY_PLAN.md·Microsoft 문서 병행**.

---

## 피해야 할 것

- **데이터 디스크·잘못된 GPT에 대한 무차별 파티션 수정**
- 검증 게이트 **FAIL** 상태에서 추가 **restore --apply**
- BootNext로 복구를 강제하는 임시 해독 코드 (정책 위반 가능)

---

**기록:** 응급 조치 버전은 `tests/manual/test_result_template.md`에 날짜·스크린샷과 함께 남기십시오.

# xorg GUI isolation — 코드 수정 결과 확인

**확인일:** 저장소 워크스페이스 기준  
**범위:** `recoverix.isolation=1` 구현 7개 파일 + GRUB `grub.cfg` 생성 경로

---

## 7개 파일 수정 확인

| 파일명 | 수정됨 / 미수정 | `ConditionKernelCommandLine=!recoverix.isolation=1` |
|--------|----------------|-----------------------------------------------------|
| `deploy/grub/recoverix-esp.cfg.template` | **수정됨** | **없음** (GRUB: `recoverix.isolation=1` L68) |
| `lib/runtime_gui_stack_install.sh` | **수정됨** | **있음** ×3 (L898, L919, L970 — boot/postboot/watchdog 유닛) |
| `lib/recovery_ui_install.sh` | **수정됨** | **있음** ×3 (L360, L383, L410 — sudo/selftest 유닛 + GDM drop-in) |
| `deploy/72_recoverix_xorg_forensic_collect.sh` | **수정됨** | **없음** (스크립트: `recoverix_xorg_isolation_mode_detected` L46–47, SKIP L171–174) |
| `deploy/75_recoverix_forensic_sudo_enable.sh` | **수정됨** | **없음** (스크립트: `recoverix_isolation_mode_detected` L37–38, SKIP L173–176, L233–235) |
| `deploy/lib/grub_boot_chain.sh` | **수정됨** | **없음** (메뉴 라벨 L262) |
| `12_verify_esp_layout.sh` | **수정됨** | **없음** (isolation menuentry 검증 L152–175) |

**결론:** 7개 **모두 수정됨.**  
`ConditionKernelCommandLine=!recoverix.isolation=1`는 **systemd 유닛/drop-in을 생성·검증하는 2개 파일**에만 존재.

---

## 파일별 추가 요약

### `deploy/grub/recoverix-esp.cfg.template`

- L61–72: `menuentry "Recoverix Runtime (xorg GUI isolation)"` + `recoverix.isolation=1`

### `lib/runtime_gui_stack_install.sh`

- `recoverix-xorg-forensic-boot.service` / postboot / watchdog 유닛에 Condition 추가
- 검증: L799–804, L815–818

### `lib/recovery_ui_install.sh`

- `recoverix-forensic-sudo.service` / selftest / GDM drop-in에 Condition 추가
- 검증: L635–677

### `deploy/72_recoverix_xorg_forensic_collect.sh`

- `recoverix_xorg_isolation_mode_detected()` + isolation SKIP 블록

### `deploy/75_recoverix_forensic_sudo_enable.sh`

- `recoverix_isolation_mode_detected()` + start/selftest SKIP

### `deploy/lib/grub_boot_chain.sh`

- `recoverix_grub_menuentry_pass_label`: `'Recoverix Runtime (xorg GUI isolation)'` case (L262)

### `12_verify_esp_layout.sh`

- isolation menuentry 검증 블록 (L152–175)

---

## isolation menuentry → 최종 `grub.cfg` 포함 여부

**포함됨** (템플릿 전체가 sed 렌더링되며 menuentry 필터 없음).

| 단계 | 위치 | 라인 |
|------|------|------|
| 템플릿 소스 | `deploy/grub/recoverix-esp.cfg.template` | `40_stage_esp_runtime.sh` L133 |
| 렌더 함수 | `recoverix_grub_render_external_menu_cfg` → `recoverix_grub_sed_render` | `grub_boot_chain.sh` L37–47, L21–34 |
| **주 출력** | `${ESP_ROOT}/EFI/RecoveryBoot/grub.cfg` | `40_stage_esp_runtime.sh` L28, L137–155 |
| legacy 복사 | `${ESP_ROOT}/EFI/Recoverix/grub.cfg` | L171–172 |
| 참조 복사 | `${ESP_DIR}/grub.cfg` | L174–175 |

`recoverix_grub_sed_render` 치환: `@RECOVERY_UUID@`, `@KERNEL_VERSION@`, `@ESP_UUID@`, `@RECOVERYBOOT_REL_DIR@` — **menuentry 삭제 없음**.

`12_verify_esp_layout.sh` L152–175: 렌더된 `grub.cfg`에서 isolation menuentry 검증.

**참고:** `40_stage_esp_runtime.sh` L249–257 `recoverix_grub_validate_xorg_forensic_menuentry`는 xorg forensic GUI/console만 검사 — isolation 전용 검증은 `12_verify_esp_layout.sh`에 있음.

---

## 빌드 시 생성 `grub.cfg` 예시

`40_stage_esp_runtime.sh` 실행 후 플레이스홀더 치환 예  
(`RECOVERY_LINUX_UUID=a0735d77-4514-4208-ad24-f8982f4370a7`, `KVER=6.8.0-117-generic`):

```grub
menuentry "Recoverix Runtime (xorg GUI isolation)" --class recoverix {
    echo "Loading Recoverix Runtime (xorg GUI isolation)..."
    search --no-floppy --fs-uuid --set=root a0735d77-4514-4208-ad24-f8982f4370a7
    linux   /boot/recoverix/vmlinuz-6.8.0-117-generic \
        recoverix.root=1 \
        recoverix.uuid=a0735d77-4514-4208-ad24-f8982f4370a7 \
        recoverix.debug.xorg=1 \
        recoverix.isolation=1 \
        root=tmpfs \
        ro quiet splash systemd.unit=graphical.target
    initrd  /boot/recoverix/initrd.img-6.8.0-117-generic-recoverix
}
```

**실제 배포 경로 (ESP 마운트 기준):**

- `/boot/efi/EFI/RecoveryBoot/grub.cfg` (부팅 시 `grubx64.efi` bootstrap이 `configfile`로 로드)

---

## 관련 문서

- `docs/XORG_GUI_ISOLATION_BOOT.md` — isolation 구현·서비스 목록
- `docs/FORENSIC_SAFE_CONSOLE_VS_XORG_GUI_UNITS.md` — safe vs xorg GUI 유닛 비교

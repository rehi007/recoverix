# xorg GUI isolation 부팅 — `recoverix.isolation=1` 구현

**목적:** xorg forensic GUI 부팅 정지 원인을 서비스 단위로 격리  
**방법:** 새 GRUB 메뉴 + `recoverix.isolation=1` 커널 파라미터로 P1/P2/P3 forensic 레이어 임시 비활성화  
**기존 메뉴:** 변경 없음

---

## 배경

| 메뉴 | 상태 |
|------|------|
| systemd debug | 정상 |
| forensic safe console | 정상 |
| xorg forensic GUI | handoff complete 이후 검은 화면 |

조사 P1–P3:

- **P1** `recoverix-xorg-forensic-boot.service`
- **P2** `recoverix-forensic-sudo.service`
- **P3** `gdm.service` + `recoverix-forensic-sudo` drop-in

---

## 1. 추가 GRUB menuentry

파일: `scripts/runtime_image/deploy/grub/recoverix-esp.cfg.template`  
위치: `Recoverix Runtime (xorg forensic GUI)` 바로 다음

```grub
menuentry "Recoverix Runtime (xorg GUI isolation)" --class recoverix {
    echo "Loading Recoverix Runtime (xorg GUI isolation)..."
    search --no-floppy --fs-uuid --set=root @RECOVERY_UUID@
    linux   /boot/recoverix/vmlinuz-@KERNEL_VERSION@ \
        recoverix.root=1 \
        recoverix.uuid=@RECOVERY_UUID@ \
        recoverix.debug.xorg=1 \
        recoverix.isolation=1 \
        root=tmpfs \
        ro quiet splash systemd.unit=graphical.target
    initrd  /boot/recoverix/initrd.img-@KERNEL_VERSION@-recoverix
}
```

| 파라미터 | isolation 메뉴 |
|----------|----------------|
| `recoverix.root=1` | ✓ |
| `recoverix.debug.xorg=1` | ✓ |
| `recoverix.isolation=1` | ✓ (신규) |
| `recoverix.safe=1` | **없음** |
| `systemd.unit` | `graphical.target` |

---

## 2. 수정 파일 목록

| 파일 | 변경 |
|------|------|
| `deploy/grub/recoverix-esp.cfg.template` | isolation 메뉴 추가 |
| `lib/runtime_gui_stack_install.sh` | boot/postboot/watchdog 유닛 `!recoverix.isolation=1` + 빌드 검증 |
| `lib/recovery_ui_install.sh` | forensic-sudo/selftest 유닛, GDM drop-in, 검증 |
| `deploy/72_recoverix_xorg_forensic_collect.sh` | isolation 시 SKIP (getty 훅 방어) |
| `deploy/75_recoverix_forensic_sudo_enable.sh` | isolation 시 SKIP (GDM drop-in 방어) |
| `deploy/lib/grub_boot_chain.sh` | 메뉴 라벨 등록 |
| `12_verify_esp_layout.sh` | isolation 메뉴 ESP 검증 |

---

## 3. `recoverix.isolation=1` 차단 메커니즘

### systemd unit — `ConditionKernelCommandLine=!recoverix.isolation=1`

| 유닛 | 정의 위치 |
|------|-----------|
| `recoverix-xorg-forensic-boot.service` | `runtime_gui_stack_install.sh` |
| `recoverix-xorg-forensic-postboot.service` | 동일 |
| `recoverix-xorg-watchdog.service` | 동일 |
| `recoverix-forensic-sudo.service` | `recovery_ui_install.sh` |
| `recoverix-forensic-sudo-selftest.service` | 동일 |

### GDM drop-in

`etc/systemd/system/gdm.service.d/recoverix-forensic-sudo.conf`:

```ini
[Unit]
ConditionKernelCommandLine=recoverix.root
ConditionKernelCommandLine=!recoverix.isolation=1

[Service]
ExecStartPre=-/usr/local/sbin/recoverix-forensic-sudo-enable start
```

### 스크립트 방어 (getty / 수동 호출)

| 스크립트 | 함수 | 동작 |
|----------|------|------|
| `72_recoverix_xorg_forensic_collect.sh` | `recoverix_xorg_isolation_mode_detected()` | isolation 시 전체 exit 0 |
| `75_recoverix_forensic_sudo_enable.sh` | `recoverix_isolation_mode_detected()` | start/selftest SKIP |

---

## 4. isolation 모드 — 실행되는 Recoverix 서비스

| 서비스 / 구성 | 비고 |
|---------------|------|
| `recoverix-xorg-forensic.service` | `--gui-trace` (`debug.xorg`만, isolation 조건 없음) |
| `recoverix-gui-forensic.service` | graphical 후 수집 |
| `recoverix-rootfs-debug.service` | `After=multi-user` |
| `recoverix-gui-fallback.timer` | timer 동작 (xorg 모드에서 fallback 스크립트는 스킵) |
| `recoverix-xorg-forensic-postboot.timer` | timer만 동작, **서비스는 isolation으로 스킵** |
| `gdm.service` / `gdm3.service` | forensic-sudo drop-in **비활성** |
| getty autologin | `recoverix.root=1` |
| `recoverix-recovery-ui` autostart | openbox 세션 시 |

---

## 5. isolation 모드 — 실행되지 않는 Recoverix 서비스/훅

| 대상 | 차단 |
|------|------|
| `recoverix-xorg-forensic-boot.service` | unit Condition |
| `recoverix-forensic-sudo.service` | unit Condition |
| `recoverix-forensic-sudo-selftest.service` | unit Condition |
| `recoverix-xorg-forensic-postboot.service` | unit Condition |
| `recoverix-xorg-watchdog.service` | unit Condition |
| GDM `ExecStartPre` forensic-sudo-enable | drop-in Condition |
| getty `ExecStartPost` postboot 수집 | `72_…collect.sh` SKIP |

---

## 6. 격리 테스트 해석

| isolation 부팅 결과 | 의미 |
|-------------------|------|
| **graphical + UI 정상** | P1/P2/P3 forensic 레이어 중 하나(또는 조합)가 xorg GUI 정지 원인 |
| **여전히 검은 화면** | GDM/Xorg/plymouth/DRM 등 **forensic 레이어 밖** 원인 |

### 런타임 확인

```bash
grep recoverix.isolation /proc/cmdline
systemctl show recoverix-xorg-forensic-boot.service -p ConditionResult,ActiveState
systemctl show recoverix-forensic-sudo.service -p ConditionResult,ActiveState
systemctl status graphical.target gdm.service
```

---

## 7. 배포

```bash
cd recovery-platform/scripts/runtime_image
sudo ./00_build_runtime.sh
sudo ./20_install_initramfs_hook.sh
sudo RECOVERY_LINUX_UUID=<p4-uuid> ./deploy/30_stage_host_boot.sh
sudo ./deploy/40_stage_esp_runtime.sh
```

부팅: **Recoverix Runtime (xorg GUI isolation)**

---

## 관련 문서

- `docs/XORG_FORENSIC_GUI_BOOT_INVESTIGATION.md`
- `docs/FORENSIC_SAFE_CONSOLE_VS_XORG_GUI_UNITS.md`

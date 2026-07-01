# graphical.target 검은 화면 조사 (GDM/Xorg/openbox 계층)

**작성 기준:** systemd debug 정상 / xorg forensic GUI·isolation 모두 handoff 후 검은 화면  
**범위:** forensic 서비스 계층 배제, GDM · graphical.target · Xorg · DRM · openbox · AccountsService · autostart  
**코드 수정:** 없음 (조사·보고만)

---

## 결론

**initramfs / overlay / run-init / PID1=systemd 경로는 정상**이며, 문제는 **`graphical.target` 진입 이후 GDM → Xorg → DRM(amdgpu) → openbox 세션** 계층에 국한됩니다.

`recoverix.isolation=1` 부팅에서도 동일한 검은 화면이면, **P1/P2/P3 forensic 서비스 계층은 1차 원인에서 배제**됩니다. 빌드 산출물(`build.log`) 기준으로 **gdm3·Xorg·openbox·AccountsService·autostart·amdgpu 펌웨어/커널 모듈은 rootfs/squashfs에 포함·검증 PASS**이므로, “패키지 미설치”보다 **런타임 GDM/Xorg/DRM 실패 + `quiet splash`로 출력 차단 + isolation 시 복구 경로 비활성** 조합이 가장 설명력이 큽니다.

---

## 가장 가능성 높은 원인 (우선순위)

| 순위 | 원인 | 이유 |
|------|------|------|
| **1** | **GDM/Xorg/DRM(amdgpu) 런타임 실패** | isolation으로 forensic 레이어를 제거해도 동일 → 핵심 GUI 스택만 남음 |
| **2** | **`quiet splash` + plymouth** | graphical 메뉴만 `quiet splash` 사용, systemd debug는 미사용 → handoff 직후 무출력·검은 화면과 일치 |
| **3** | **isolation 시 watchdog/fallback 복구 경로 모두 차단** | Xorg 실패 시 multi-user로 자동 전환되지 않아 **영구 검은 화면** 가능 |
| **4** | **커스텀 Xorg 설정 부재** | amdgpu/fbdev 드라이버는 설치되나 `/etc/X11/xorg.conf.d/` Recoverix 스니펫 없음 → 하드웨어/DRM 열거 실패 시 fbdev fallback 보장 없음 |
| **5** | **`display-manager.service` / `default-display-manager` 빌드 미검증** | `gdm.service` enable은 검증하나 DM alias 파일은 검증 안 함 (gdm3 postinst 의존) |

---

## 7개 확인 항목별 분석

### 1. `graphical.target`에서 실제 display-manager

Ubuntu 표준 체인:

```text
graphical.target
  └─ display-manager.service  (alias)
       └─ gdm.service  (jammy gdm3 패키지)
            └─ /usr/sbin/gdm3
```

Recoverix 빌드는 **`gdm.service`를 enable**하고 default target을 `graphical.target`으로 설정합니다.

- 정의: `lib/runtime_gui_stack_install.sh` → `runtime_configure_graphical_target()`
  - `systemctl set-default graphical.target`
  - `systemctl enable gdm.service` (fallback: `gdm3.service`)
- **`gdm3.service`**: jammy에서 보통 `gdm.service`와 동일 체인; 코드는 `gdm.service` 우선.
- **`/etc/X11/default-display-manager`**: Recoverix 스크립트에서 **명시 생성/검증 없음** → gdm3 deb postinst에 의존.
- **`/etc/systemd/system/display-manager.service`**: 빌드 검증 **없음** (런타임 forensic 수집 스크립트에서만 확인).

### 2. gdm/gdm3가 runtime.squashfs에 완전 설치되었는가

**빌드 시점 PASS** (`scripts/runtime_image/build.log`):

- `apt install OK: gdm3`
- `enabled gdm.service`
- `gdm binary: /usr/sbin/gdm3`
- `gdm unit: gdm.service`
- `/usr/bin/Xorg`, `/usr/lib/xorg/modules` 존재
- `graphical.target` default

gdm3 허용 의존 체인: `assets/runtime_gdm3_allowed_dependencies.txt` (gnome-shell, mutter, accountsservice 등 포함).

**갭**: `display-manager.service` symlink·`/etc/X11/default-display-manager`는 빌드 verify 대상 아님.

### 3. openbox 세션이 GDM에서 선택 가능한가

**설계·빌드상 가능**:

| 구성 | 경로/내용 |
|------|-----------|
| xsessions | `/usr/share/xsessions/openbox.desktop` (openbox 패키지) |
| AccountsService | `/var/lib/AccountsService/users/recoverix` → `Session=openbox` |
| 사용자 .dmrc | `~recoverix/.dmrc` → `Session=openbox` |
| GDM autologin | `/etc/gdm3/custom.conf` → `WaylandEnable=false`, `AutomaticLogin=recoverix` |

설치: `lib/recovery_ui_install.sh` → `runtime_install_recoverix_runtime_autologin()`  
AccountsService: `lib/runtime_gui_stack_install.sh` → `runtime_install_recoverix_accounts_service_user()`

`build.log`: `accountsservice_validation_result=PASS`, `openbox xsession` PASS.

**주의**: GDM autologin이 성공하려면 **GDM 자체가 먼저 기동**되어야 함. 검은 화면은 그 이전(GDM/Xorg/DRM) 단계 실패 가능성이 큼.

### 4. recoverix UI autostart가 로그인 세션에서 실행 가능한가

**파일은 rootfs에 설치·검증됨**:

| 항목 | 경로 |
|------|------|
| autostart | `/etc/xdg/autostart/recoverix-recovery-ui.desktop` |
| launcher | `/usr/local/sbin/recoverix-recovery-ui` → `python3 -m recovery_runtime.gtk_ui.main` |

`assets/recoverix-recovery-ui.desktop`:

- `Exec=/usr/local/sbin/recoverix-recovery-ui`
- `OnlyShowIn=...;Openbox;` → openbox 세션 조건 충족

openbox 세션이 올라오면 autostart 조건 충족. 다만 autostart는 **openbox 세션 이후** 단계이므로, 현재 검은 화면의 **직접 원인은 아님** (2차 증상만 해당).

### 5. amdgpu/Xorg/fbdev 설정 누락

**설치됨 (빌드 PASS)**:

- 패키지: `xserver-xorg-video-amdgpu`, `xserver-xorg-video-fbdev` (`assets/runtime_gui_packages.txt`)
- debootstrap seed: `xserver-xorg-video-dummy` (`assets/runtime_debootstrap_packages.txt`)
- 펌웨어: `lib/firmware_provision.sh` → `runtime_ensure_amdgpu_firmware()` → `/usr/lib/firmware/amdgpu`
- 커널 모듈: `linux-modules-extra-${KERNEL_VERSION}` 검증 (`10_build_squashfs.sh`, `firmware_provision.sh`)

**누락/갭**:

- Recoverix 전용 **`/etc/X11/xorg.conf` 또는 `xorg.conf.d/*.conf` 없음** (repo 전체 grep 0건)
- **modesetting** 드라이버 패키지 명시 없음 (amdgpu 실패 시 자동 fallback 경로 불명확)
- **런타임 `modprobe amdgpu` / DRM 노드 생성**은 빌드에서 검증 불가 — 실기기 `dmesg`, `/var/log/Xorg.0.log` 필요

### 6. `recoverix.isolation=1` 시 forensic 비활성 목록 적용

**코드·빌드 검증상 적용됨** (`ConditionKernelCommandLine=!recoverix.isolation=1`):

| 비활성 대상 | 정의 위치 |
|-------------|-----------|
| `recoverix-xorg-forensic-boot.service` | `lib/runtime_gui_stack_install.sh` |
| `recoverix-xorg-forensic-postboot.service` | 동일 |
| `recoverix-xorg-watchdog.service` | 동일 |
| `recoverix-forensic-sudo.service` | `lib/recovery_ui_install.sh` |
| `recoverix-forensic-sudo-selftest.service` | 동일 |
| GDM `ExecStartPre` forensic-sudo drop-in | `gdm.service.d/recoverix-forensic-sudo.conf` |
| `72_recoverix_xorg_forensic_collect.sh` / `75_recoverix_forensic_sudo_enable.sh` | 스크립트 early-exit |

**isolation에서도 계속 실행**:

| 유지 실행 | 조건 |
|-----------|------|
| `recoverix-xorg-forensic.service` | `recoverix.debug.xorg=1` only (isolation 조건 **없음**) |
| `recoverix-gui-forensic.service` | 조건 없음 |
| `gdm.service` | 표준 (forensic drop-in만 스킵) |

**복구 경로 차단 (isolation 시 치명적)**:

- `recoverix-xorg-watchdog.service` → **스킵** → 20초 후 multi-user 전환 없음
- `deploy/71_recoverix_gui_fallback_check.sh` L16–19: `recoverix.debug.xorg=1`이면 **fallback 자체 스킵**

→ isolation 부팅은 **GDM/Xorg 실패 시 자동 복구 없이 검은 화면 고착** 가능.

### 7. xorg GUI isolation 메뉴가 `graphical.target`으로 부팅하는가

**예, 명시적으로 설정됨** (`deploy/grub/recoverix-esp.cfg.template` L61–71):

```text
recoverix.isolation=1
...
ro quiet splash systemd.unit=graphical.target
```

`12_verify_esp_layout.sh`에서 `systemd.unit=graphical.target` 검증 포함.

**systemd debug와의 차이** (핵심):

| 항목 | xorg GUI / isolation | systemd debug |
|------|----------------------|---------------|
| `systemd.unit` | `graphical.target` | `multi-user.target` |
| `recoverix.safe=1` | 없음 | **있음** |
| `quiet splash` | **있음** | **없음** |
| GDM 기동 | **예** | **아니오** (getty autologin) |

---

## 근거 파일/유닛 요약

| 영역 | 파일 |
|------|------|
| GRUB cmdline | `scripts/runtime_image/deploy/grub/recoverix-esp.cfg.template` L49–71 |
| GDM enable / graphical default | `scripts/runtime_image/lib/runtime_gui_stack_install.sh` |
| GDM custom.conf / autologin | `scripts/runtime_image/lib/recovery_ui_install.sh` |
| AccountsService + openbox | `scripts/runtime_image/lib/runtime_gui_stack_install.sh` |
| GUI 패키지 목록 | `scripts/runtime_image/assets/runtime_gui_packages.txt` |
| gdm3 의존 allowlist | `scripts/runtime_image/assets/runtime_gdm3_allowed_dependencies.txt` |
| forensic/isolation 유닛 | `scripts/runtime_image/lib/runtime_gui_stack_install.sh` |
| forensic sudo / GDM drop-in | `scripts/runtime_image/lib/recovery_ui_install.sh` |
| UI autostart | `scripts/runtime_image/assets/recoverix-recovery-ui.desktop` |
| amdgpu 펌웨어/모듈 | `scripts/runtime_image/lib/firmware_provision.sh`, `10_build_squashfs.sh` |
| fallback/watchdog (복구 차단) | `deploy/71_recoverix_gui_fallback_check.sh`, `deploy/74_recoverix_xorg_watchdog.sh` |
| 빌드 PASS 증거 | `scripts/runtime_image/build.log` |
| isolation 설계 | `docs/XORG_GUI_ISOLATION_BOOT.md` |
| xorg vs systemd debug | `docs/XORG_FORENSIC_GUI_BOOT_INVESTIGATION.md` |

---

## 수정 후보 (제안만, 미적용)

1. **진단 우선**: isolation 메뉴에서 `quiet splash` 제거 + `systemd.log_level=debug systemd.show_status=1` 추가한 **임시 GRUB 변형**으로 `journalctl -b -u gdm -u display-manager`, `/var/log/Xorg.0.log` 확보.

2. **Xorg fallback 스니펫**: `/etc/X11/xorg.conf.d/10-recoverix-drm.conf`에 amdgpu 실패 시 `fbdev`/`modesetting` Device/ServerLayout 명시 (빌드 시 rootfs에 설치).

3. **isolation 시 watchdog 유지**: `recoverix-xorg-watchdog.service`에 `!recoverix.isolation=1` 제거 또는 isolation 전용 watchdog 추가 → Xorg 20초 미기동 시 `multi-user.target` isolate.

4. **`71_recoverix_gui_fallback_check.sh`**: isolation 모드에서도 GDM inactive 시 fallback 허용 (`debug.xorg=1` 스킵 로직 조정).

5. **빌드 검증 보강**: `display-manager.service` symlink, `/etc/X11/default-display-manager`, `systemctl is-enabled display-manager.service` chroot 검증 추가.

6. **`recoverix-xorg-forensic-postboot.timer`**: timer는 isolation에서도 동작하나 서비스는 스킵 — timer에 `!recoverix.isolation=1` 추가해 불필요 wake 제거 (부수적).

---

## 실기기 확인 명령 (다음 단계)

isolation 부팅 후 (가능하면 `quiet splash` 없는 변형으로):

```bash
grep -E 'recoverix\.(root|isolation|debug\.xorg)' /proc/cmdline
systemctl is-active graphical.target display-manager.service gdm.service
systemctl show recoverix-xorg-forensic-boot.service -p ConditionResult,ActiveState
systemctl show recoverix-xorg-watchdog.service -p ConditionResult,ActiveState
journalctl -b -u gdm.service -u display-manager.service --no-pager
ls -l /etc/systemd/system/display-manager.service /etc/X11/default-display-manager
cat /etc/gdm3/custom.conf
lsmod | grep -E 'amdgpu|drm'
ls -la /dev/dri/
tail -100 /var/log/Xorg.0.log
```

---

## 관련 문서

- [XORG_FORENSIC_GUI_BOOT_INVESTIGATION.md](./XORG_FORENSIC_GUI_BOOT_INVESTIGATION.md)
- [XORG_GUI_ISOLATION_BOOT.md](./XORG_GUI_ISOLATION_BOOT.md)
- [FORENSIC_SAFE_CONSOLE_VS_XORG_GUI_UNITS.md](./FORENSIC_SAFE_CONSOLE_VS_XORG_GUI_UNITS.md)

# Recovery Runtime rootfs 경량화 (Ubuntu 22.04 Desktop → Minimal)

**대상만 수정:** `/recovery/build/rootfs`  
**절대 금지:** 호스트 `/` chroot, `ROOTFS=/` 설정, 호스트 패키지 `apt purge`

**목표 용량:** 1–3 GB (압축·배포 형태에 따라 상이)  
**현재 관측:** ~12 GB (Desktop + snapd 2.1G + swapfile 2G + linux-firmware 1.1G + cursor/chrome/LibreOffice 등)

스크립트 위치: `recovery-platform/scripts/rootfs_minimize/`

---

## 1. 실행 순서 (권장)

| 단계 | 명령 | 설명 |
|------|------|------|
| 0 | `sudo ./06_snapshot_rootfs.sh pre-minimize` | **롤백용 스냅샷** (필수) |
| 1 | `sudo ./08_preflight_run.sh tier1` | **권장:** 분석·plan·gate·요약 일괄 (read-only) |
| 2 | `sudo APT_DRY_RUN=0 ./03_minimize_apply.sh tier1` | **exit 0 preflight 후** 실제 purge (`08` 자동 호출) |
| 3 | (선택) tier2 | GNOME 제거 — X11+GTK 정책 확정 후 |
| 4 | `du -sh /recovery/build/rootfs` | 최종 용량 확인 |

수동 단계가 필요하면 `01` → `02` → `07` 순으로 개별 실행 가능합니다.

---

## 2. chroot 진입 (수동 작업 시)

```bash
cd /path/to/recoverix/recovery-platform/scripts/rootfs_minimize
sudo ./00_chroot_enter.sh
# 작업 후 exit
sudo ./99_chroot_leave.sh
```

내부에서만:

```bash
apt purge ...
apt autoremove --purge
apt clean
```

---

## 3. 삭제 가능 패키지 (Tier 1 — 우선)

| 항목 | 예상 절감 | 비고 |
|------|-----------|------|
| **snapd** + `/var/lib/snapd` | ~2.3 GB | 데이터 디렉터리 별도 삭제 |
| **cursor** | ~850 MB | IDE, 런타임 불필요 |
| **google-chrome-stable** | ~400 MB | |
| **thunderbird** | ~300 MB | |
| **libreoffice\*** | ~400 MB+ | help/l10n 포함 |
| **linux-firmware** (전체) | ~1.1 GB | 필요 NIC/WiFi만 나중에 선별 재설치 |
| **구버전 커널 6.8.0-40** | ~700 MB+ | `KEEP_KERNEL_FLAVOR` 하나만 유지 |
| **swapfile** (rootfs 루트) | 2 GB | 이미지에 포함된 파일 |
| doc/man/help | ~125 MB | `04_prune_filesystem.sh` |
| apt cache/lists | ~400 MB | prune + clean |
| journal | ~137 MB | volatile journal 설정 |

**Tier 1 합계 (대략):** 5–8 GB 절감 가능 → **4–7 GB** 수준까지 1차 하향

---

## 4. 삭제 금지 패키지 (핵심)

`packages_keep.txt` 참고. 요약:

- **python3**, **python3-gi**, **gir1.2-gtk-3.0**, **libgtk-3-0**
- **partclone**, **ntfs-3g**, **e2fsprogs**, **gdisk**, **parted**
- **grub-efi-amd64-bin**, **grub-efi-amd64-signed**, **shim-signed**, **efibootmgr**
- **initramfs-tools**, **busybox-initramfs**
- **systemd** (최소), **network-manager**, **iproute2**
- **X11 최소:** `xserver-xorg-core`, `xinit`, `libx11-6`

---

## 5. Tier 2 (GNOME 데스크톱 — 신중)

`packages_purge_tier2.txt`: gdm3, gnome-shell, ubuntu-session 등.

**위험:** 로그인 세션·폰트·ibus 제거 시 GTK TUI만으로 운영해야 함.  
**대안:** `lightdm` + `openbox` 또는 `startx` + Recovery 앱 단독 실행.

Tier2 적용 전 반드시:

1. `02_generate_purge_plan.sh tier2`
2. `05_verify_runtime.sh` (GTK import)
3. 실기기에서 X11 GTK 창 수동 테스트

**Wayland:** 불필요 시 Tier2에서 `mutter`, `gnome-shell-wayland` 제거 가능.

---

## 6. 자동 생성 purge 명령

```bash
sudo ./02_generate_purge_plan.sh tier1
# → /recovery/build/reports/rootfs-minimize/purge_plan_tier1_*.sh
```

실제 적용:

```bash
sudo APT_DRY_RUN=0 ./03_minimize_apply.sh tier1
```

내부 동작:

```text
apt purge <tier 목록>
apt autoremove --purge
apt clean
```

---

## 7. 파일시스템 최소화 (`04_prune_filesystem.sh`)

- `/usr/share/doc`, `man`, `help` 비움
- apt archives/lists 정리
- journal → volatile, 16M 상한
- locale: `en_US.UTF-8`, `ko_KR.UTF-8` 위주

---

## 8. Pre-purge 안전성 게이트 (`07_pre_purge_safety_gate.sh`)

Tier1 **apply 전 필수**. 호스트 손상 방지 + boot/runtime 패키지 보호.

| 검사 | 내용 |
|------|------|
| Host safety lock | `ROOTFS=/` 금지, host root 동일 금지, host bind mount 감지 |
| Boot critical | `packages_boot_critical.txt` 패턴 + **KEEP_KERNEL_FLAVOR** 커널만 동적 보호 (구 커널 purge 허용) |
| apt simulate | REMV에 grub/shim/kernel/python3-gi/partclone 등 포함 시 **즉시 FAIL** |
| Dependency tree | `apt-rdepends` anchor closure 충돌 검사 |
| Kernel | `KEEP_KERNEL_FLAVOR` vmlinuz/initrd/modules |
| EFI | `shimx64.efi`, `grubx64.efi`, `grub.cfg` (없으면 WARN) |
| Bootability | `update-initramfs`, `grub-mkconfig` (실패 시 중단) |
| GTK | gi import, Gtk.init_check, window create/destroy |
| SquashFS readiness | broken symlink, `dpkg --audit`, ldconfig |
| Size report | total/usr/var/firmware/cache + squashfs 추정 |

```bash
sudo ./07_pre_purge_safety_gate.sh tier1
# 빠른 검사만 (initramfs/grub 재생성 생략):
sudo RUN_BOOTABILITY=0 ./07_pre_purge_safety_gate.sh tier1
```

리포트: `/recovery/build/reports/rootfs-minimize/pre_purge_gate_*.log`

`05_verify_runtime.sh`는 동일 게이트로 위임됩니다.

---

## 9. Preflight Runner (`08_preflight_run.sh`)

Tier1 **실제 purge 전** 한 번에 실행하는 read-only 검증 파이프라인입니다. 호스트 Ubuntu는 수정하지 않으며 `/recovery/build/rootfs`만 검사합니다.

```bash
cd recovery-platform/scripts/rootfs_minimize
sudo ./08_preflight_run.sh tier1
```

### 자동 실행 순서

1. `06_snapshot_rootfs.sh preflight-before-tier1` — 24h 이내 동일 스냅샷이 있으면 건너뜀 (`SKIP_SNAPSHOT=1`로 생략 가능)
2. `01_analyze_rootfs.sh`
3. `02_generate_purge_plan.sh tier1`
4. `07_pre_purge_safety_gate.sh tier1` (`RUN_BOOTABILITY=0` 기본)
5. 최신 `pre_purge_gate_*.log` 분석 → 요약 생성

내부적으로 `APT_DRY_RUN=1` 정책을 강제합니다. **실제 apt purge는 수행하지 않습니다.**

### 산출물

| 파일 | 경로 |
|------|------|
| `preflight_summary_<timestamp>.txt` | `/recovery/build/reports/rootfs-minimize/` |
| `preflight_summary_<timestamp>.json` | 동일 |
| `preflight_steps_<timestamp>.log` | 파이프라인 단계 OK/FAIL |

### 결과 해석 (PASS / WARN / FAIL)

| 상태 | 의미 |
|------|------|
| **PASS** | 해당 검사 통과 |
| **WARN** | 진행 가능하나 검토 필요 (EFI 미포함 등 흔함) |
| **FAIL** | Tier1 purge **금지** |

### Exit code

| 코드 | 의미 | Tier1 purge |
|------|------|-------------|
| **0** | PASS only | 진행 가능 (검토 후 apply) |
| **1** | WARN only | **기본 차단** — `FORCE_WARN=1` 없이 `03` apply 금지 |
| **2** | FAIL ≥1 | **절대 금지** |

### purge 진행 가능 조건

1. `sudo ./08_preflight_run.sh tier1` → **exit 0**
2. `preflight_summary_*.txt`에서 `purge_allowed: true` 확인
3. 그 후에만:

```bash
sudo APT_DRY_RUN=0 ./03_minimize_apply.sh tier1
```

`03_minimize_apply.sh`는 `APT_DRY_RUN=0` 시 **자동으로 `08`을 호출**합니다. preflight `exit 2`면 apply가 즉시 중단됩니다.

### FAIL/WARN 분류·조치

| 분류 | 조치 요약 |
|------|-----------|
| HOST_SAFETY | ROOTFS·config.env·mount 즉시 확인 |
| BOOT_CRITICAL | purge 목록에 keep 커널 포함 여부 확인 — 구 커널만 tier1에 유지 |
| APT_REMV_CRITICAL | apt simulate REMV — plan 수정 전 purge 금지 |
| DEPENDENCY | apt-rdepends 충돌, 삭제 후보 재검토 |
| KERNEL | `KEEP_KERNEL_FLAVOR` / boot artifacts |
| EFI | rootfs에 ESP 없을 수 있음 (WARN 흔함); 배포 전 실제 ESP 확인 |
| BOOTABILITY | initramfs/grub 실패 시 purge 금지 |
| GTK | python3-gi / GTK / X11 최소 스택 유지 |
| SQUASHFS_READY | symlink/dpkg/ldconfig 수정 |
| SIZE | 정보성 (FAIL 아님) |

환경 변수: `SKIP_SNAPSHOT=1`, `RUN_BOOTABILITY=1` (부트 재생성 검사 포함, 느림)

**SQUASHFS_READY WARN** (broken symlinks 등) 시:

```bash
sudo ./09_repair_rootfs_integrity.sh
sudo SKIP_SNAPSHOT=1 ./08_preflight_run.sh tier1
```

### Rootfs integrity repair (`09_repair_rootfs_integrity.sh`)

Tier1 purge **전** rootfs 무결성만 정리합니다 (apt purge 없음).

- `find ROOTFS -xtype l` — dangling symlink 탐지·분류
- 보호: `/boot`, `/lib/modules`, GRUB, EFI, python3/GTK/partclone 관련
- 제거 허용: cache, snap 잔재, desktop/doc/man, chrome/firefox/cursor 잔재 등
- `ldconfig`, `dpkg --audit`, `dpkg -C`, `update-initramfs` sanity
- repair 후 symlink/GTK/partclone 재검사

리포트: `broken_symlink_repair_<timestamp>.txt`, `rootfs_integrity_repair_<timestamp>.txt`

`DRY_RUN=1` — 제거 없이 보고만.

### Snapd chroot-safe purge (`lib/snapd_cleanup.sh`)

Tier1 apply 시 `snapd` purge는 host `/run` bind를 **해제한 chroot**에서 수행합니다 (host snapd/namespace 미접촉). 실패 시 rootfs 내부 force cleanup + `dpkg --configure -a` / `apt -f install`로 복구하며, **recoverable WARN**으로 전체 apply를 중단하지 않습니다. 리포트: `snapd_cleanup_<timestamp>.txt`

**Symlink 분류** (`lib/symlink_classifier.sh`): broken symlink를 INTENTIONAL / REMOVABLE / PACKAGE_MANAGED / RUNTIME_DYNAMIC / BOOT_CRITICAL / GTK_CRITICAL / UNKNOWN 으로 분류합니다. preflight WARN은 **warnable**(UNKNOWN·BOOT_CRITICAL·GTK_CRITICAL)만 집계하며, intentional dangling은 허용됩니다. 리포트: `broken_symlink_classification_<timestamp>.txt`

### 스크립트 exit code 표준

| 코드 | 의미 |
|------|------|
| 0 | PASS |
| 1 | WARN |
| 2 | FAIL |
| 10–19 | 내부 recoverable warning |
| 20–39 | 도구/런타임 내부 상태 (예: util-linux mount **32** = mount point 없음 — `mount_chroot_fs`가 디렉터리 생성 후 재시도) |
| ≥40 | hard failure |

`08_preflight_run.sh`는 gate log 기준으로 `purge_allowed`/`recommended_exit_code`를 결정하며, gate PASS 시 pipeline `FAIL_STEP`은 summary에 표시하지 않습니다.

---

## 10. 최종 검증 (`05_verify_runtime.sh` → `07`)

| 검사 | 내용 |
|------|------|
| python3-gi / Gtk 3 | `import gi; from gi.repository import Gtk` |
| partclone | `partclone.ntfs -V` |
| NTFS | `ntfsinfo`, `ntfs-3g` |
| GRUB | `grub-mkconfig` |
| EFI | `efibootmgr` |
| initramfs | `update-initramfs -u -k <KEEP_KERNEL>` |

---

## 11. 위험 요소

| 위험 | 완화 |
|------|------|
| 호스트 손상 | `ROOTFS` 경로 검증, `/` 거부 |
| GTK 깨짐 | tier1만 먼저, `packages_keep.txt` 시뮬레이션 |
| 부팅 실패 | 커널 1개만 남기기, initramfs 재생성 검증 |
| WiFi/GPU 없음 | `linux-firmware` 전량 삭제 시 해당 하드웨어 드라이버 복구 필요 |
| Secure Boot | **shim-signed**, **grub-efi-amd64-signed** 유지 |

---

## 12. Rollback

```bash
# 생성
sudo ./06_snapshot_rootfs.sh pre-minimize-YYYYMMDD

# 복구 (chroot 언마운트 후)
sudo rm -rf /recovery/build/rootfs
sudo mkdir -p /recovery/build/rootfs
sudo zstd -d /recovery/build/snapshots/rootfs_pre-minimize-YYYYMMDD.tar.zst -c \
  | sudo tar -C /recovery/build -xf -
```

---

## 13. 1–3 GB 목표 달성 전략

Desktop ISO를 purge만으로 1 GB까지 내리기는 어렵습니다. **2단계 전략** 권장:

1. **1단계 (본 문서):** 기존 rootfs에서 Tier1+prune → **~3–5 GB** 목표  
2. **2단계 (권장):** `debootstrap --variant=minbase jammy` 로 새 rootfs 빌드 후 `packages_keep.txt`만 선별 설치 → **1–2 GB** 달성 용이

상업용 Recovery에는 2단계가 재현성·보안 면에서 유리합니다.

---

## 14. Secure Boot

유지 패키지:

- `shim-signed`
- `grub-efi-amd64-signed`
- MOK/벤더 키 정책은 별도 OEM 문서 (`docs/SECURE_BOOT.md`) 참고

---

## 관련

- Recovery 플랫폼 코드: `recovery-platform/`
- Runtime 정책: `docs/RECOVERY_RUNTIME.md`

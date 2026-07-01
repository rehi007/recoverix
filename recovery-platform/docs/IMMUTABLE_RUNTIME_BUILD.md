# Recoverix immutable Recovery Runtime image build

상업용 Recovery Runtime의 **이미지 생성 단계** 문서입니다.  
**호스트 Ubuntu 부트 체인은 변경하지 않습니다** (EFI/grub/shim/boot order).

## 구조

```text
EFI (배포 단계)
 → grub
   → kernel (KEEP_KERNEL_FLAVOR)
     → initramfs (recoverix-overlay)
       → squashfs readonly  (/run/rootfs-base)
       → tmpfs overlay      (/run/rootfs-overlay + work)
       → merged root        (/run/rootfs-root)
         → Python GTK Recovery UI
```

## Production rootfs (debootstrap, primary)

기본 빌드 경로는 **host `/` rsync 클론이 아닌** `debootstrap` 기반 최소 Ubuntu runtime seed입니다.

| 항목 | 설명 |
|------|------|
| Suite / variant | `jammy` + `minbase` (기본) |
| Seed 패키지 | `scripts/runtime_image/assets/runtime_debootstrap_packages.txt` |
| Release map | `assets/runtime_package_release_map.txt` (jammy: `ntfsprogs` → `ntfs-3g`) |
| 패키지 검증 | `recoverix_validate_runtime_package()` — apt-cache, obsolete 스킵, replacement 제안 |
| 제외 검증 | `assets/runtime_exclude_packages.txt` (snapd, firefox 등) |
| 필수 바이너리 | `ntfs-3g`, `mount.ntfs`, `partclone.ntfs` (rootfs 내 경로 검증) |
| 크기 목표 | rootfs **1–3 GiB** (warn >3 GiB, fail >5 GiB) |
| Source marker | `rootfs/etc/recoverix/runtime-rootfs-source` |

### Runtime rootfs source mode (로그·리포트)

| Mode | 의미 |
|------|------|
| `debootstrap` | production minimal seed로 새로 빌드 |
| `snapshot` | 기존 minimal rootfs 재사용 (marker 또는 무 marker legacy) |
| `dev-host-rsync` | `RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP=1` 개발 비상 모드만 |

### 환경 변수

| 변수 | 기본값 | 의미 |
|------|--------|------|
| `RECOVERIX_RUNTIME_BUILD_MODE` | `debootstrap` | `debootstrap` \| `auto` \| `snapshot-only` |
| `RECOVERIX_FORCE_DEBOOTSTRAP` | `0` | `1`이면 기존 rootfs wipe 후 debootstrap 재빌드 |
| `RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP` | `0` | `1`만 host `/` rsync (비상 개발) |
| `RECOVERIX_ALLOW_HOST_CLONE_SNAPSHOT` | `0` | host-clone-like rootfs 스냅샷 허용 (비권장) |
| `KEEP_RUNTIME_ROOTFS` | `1` | `0`이면 squashfs 성공 후 rootfs를 `rootfs.bak.<ts>`로 이동 |
| `RUNTIME_DEBOOTSTRAP_MIN_FREE_GB` | `12` | debootstrap 전 최소 여유 공간 |

### 권장 빌드

```bash
cd recovery-platform/scripts/runtime_image
export RECOVERIX_FORCE_DEBOOTSTRAP=1   # 최초 전환 또는 host clone 제거 시
export KEEP_RUNTIME_ROOTFS=0         # squashfs 후 rootfs 디스크 회수 (선택)
sudo ./00_build_runtime.sh
```

스토리지 절약: squashfs만 유지하려면 `KEEP_RUNTIME_ROOTFS=0`. 디버그 시 `KEEP_RUNTIME_ROOTFS=1`.

### Ubuntu jammy NTFS 의존성 (migration)

- **`ntfsprogs`**: jammy에서 제거됨(obsolete). seed에 넣지 않음.
- **`ntfs-3g`**: NTFS 마운트·도구 제공 (`/usr/bin/ntfs-3g`, `/usr/sbin/mount.ntfs`).
- **`partclone`**: `partclone.ntfs` 제공 (Ubuntu는 보통 `/usr/sbin/partclone.ntfs`).

debootstrap 설치 전 `runtime_package_release_map.txt`로 obsolete 패키지를 건너뛰고 replacement를 설치 목록에 합칩니다.

### Runtime apt repository (universe / partclone)

debootstrap `minbase`는 기본적으로 **main** 만 켭니다. production runtime은 rootfs `/etc/apt/sources.list` 를 명시적으로 초기화합니다:

| 항목 | 값 |
|------|-----|
| components | main, restricted, **universe**, multiverse |
| suites | jammy, jammy-updates, jammy-security |
| template | `assets/runtime_apt_sources.jammy.list` |

**partclone** 은 Ubuntu jammy **universe** 패키지입니다. rootfs에 universe가 없으면 호스트에서 `apt-cache policy partclone` 이 되어도 chroot 설치는 실패합니다.

빌드 순서: `configure runtime apt repositories` → `apt-get update` → `apt-cache policy partclone` 검증 → seed install → 리포트 `runtime_apt_repos_*.txt`.

### Strict minimal purity (desktop 차단)

- `recoverix_runtime_apt_install`: 패키지별 설치, `--no-install-recommends`, simulate로 excluded 패키지 유입 차단.
- 제외 목록: `assets/runtime_exclude_packages.txt` (`gnome-shell`, `ubuntu-desktop`, `snapd`, `libreoffice*`, `gdm3`, `mutter`, …).
- GTK/X11 최소 seed: `libgtk-3-0`, `python3-gi`, `xserver-xorg-core`, `xserver-xorg-video-dummy` — `xinit`/full desktop metapackage 없음.
- purity FAIL 시: `runtime_excluded_dependency_trace_*.txt` 에 dependency chain 기록.

## 스크립트 위치

`recovery-platform/scripts/runtime_image/`

| 스크립트 | 역할 |
|----------|------|
| `00_build_runtime.sh` | 전체 파이프라인 (provision → tools → squashfs → initramfs → verify) |
| `lib/runtime_debootstrap.sh` | debootstrap provision, size/bloat 검증, optional rootfs cleanup |
| `10_build_squashfs.sh` | `runtime.squashfs` (xz, noappend) |
| `20_install_initramfs_hook.sh` | initramfs hook + rootfs 내 initrd 재생성 |
| `11_verify_runtime_boot.sh` | loop mount / overlay / GTK 검증 |
| `deploy/grub/recoverix-esp.cfg.template` | RecoveryBoot ESP GRUB 템플릿 |

## 실행

```bash
cd recovery-platform/scripts/runtime_image
sudo ./00_build_runtime.sh
```

## 산출물

- `/recovery/build/runtime/runtime.squashfs`
- `host:/boot/recoverix/runtime.squashfs` (`deploy/30_stage_host_boot.sh` — rootfs에 넣지 않음)
- `/recovery/build/rootfs/boot/vmlinuz-*`, `initrd.img-*` (또는 `KEEP_RUNTIME_ROOTFS=0` 시 `rootfs.bak.*`에 보관)
- `/recovery/build/reports/runtime-build/*.txt` (artifact inventory 포함)

## 부팅 후 기대 (배포 연결 후)

```bash
mount | grep "overlay on /"
python3 -c "import gi; from gi.repository import Gtk; Gtk.init_check(None)"
cat /etc/recoverix/runtime-rootfs-source   # squashfs 내 marker (빌드 시 기록)
```

## 금지 (이 단계)

- `efibootmgr`, `grub-install`, `update-grub` (host)
- shim / bootmgfw 교체
- F5 hotkey / boot order 변경
- initramfs overlay chain / restore execution 로직 변경

## 관련

- [`ROOTFS_MINIMIZE.md`](ROOTFS_MINIMIZE.md) — 레거시 rootfs 경량화·스냅샷
- [`RECOVERY_RUNTIME.md`](RECOVERY_RUNTIME.md) — 런타임 앱 정책

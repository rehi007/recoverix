# Recoverix — Host 첫 실부팅 테스트 (안전 절차)

**목표:** Ubuntu / Windows / Recoverix Runtime **공존** GRUB 메뉴에서 Recoverix만 추가 테스트  
**금지:** `grub-install`, EFI/shim 덮어쓰기, `efibootmgr` BootOrder 변경, `GRUB_DEFAULT` 변경

## 이 PC 환경 (관측값)

| 항목 | 값 |
|------|-----|
| Ubuntu root | `nvme0n1p3` UUID `2444754b-2a4b-42a0-9be1-e02080850955` |
| Windows | `nvme0n1p2` (NTFS) |
| 미마운트 ext4 | `nvme0n1p4` UUID `a0735d77-4514-4208-ad24-f8982f4370a7` (향후 전용 RECOVERY 파티션 후보) |
| 펌웨어 BootCurrent | **RecoveryBoot** (0001) — 주의 |
| Ubuntu GRUB | Boot0002 `ubuntu` → `/etc/grub.d` + `update-grub` |

**중요:** 펌웨어 기본이 RecoveryBoot이면 재부팅 후 **부팅 메뉴에서 `ubuntu` 항목**을 선택해야 아래 GRUB 3종 메뉴가 보입니다.

## GRUB 전략: `41_recoverix` (권장)

| 방식 | 장점 | 단점 |
|------|------|------|
| **`/etc/grub.d/41_recoverix`** ✅ | 롤백 쉬움 (`rm` + `update-grub`), Ubuntu/Windows 스크립트와 분리 | `update-grub` 필요 |
| `40_custom` | 수동 편집 | 실수·중복 위험 |
| `/boot/grub/grub.cfg` 직접 편집 | ❌ | `update-grub` 시 덮어씀 |

**하지 않는 것:** `grub-install`, ESP shim 교체, `GRUB_DEFAULT` 변경

## 실제 GRUB menuentry (생성 후)

`31_install_grub_entry.sh`가 UUID를 치환해 설치합니다. 내용 예:

```grub
menuentry "Recoverix Runtime (immutable overlay)" --class recoverix {
    search --no-floppy --fs-uuid --set=root 2444754b-2a4b-42a0-9be1-e02080850955
    linux   /boot/vmlinuz-6.8.0-117-generic \
        root=UUID=2444754b-2a4b-42a0-9be1-e02080850955 \
        recoverix.root=1 \
        recoverix.uuid=2444754b-2a4b-42a0-9be1-e02080850955 \
        ro quiet splash
    initrd  /boot/recoverix/initrd.img-6.8.0-117-generic-recoverix
}
```

- **Host Ubuntu initrd** (`/boot/initrd.img-6.8.0-117-generic`)는 **그대로 유지**
- Recoverix 전용 initrd: `/boot/recoverix/initrd.img-*-recoverix`
- squashfs: `/boot/recoverix/runtime.squashfs`

## 실행 순서

```bash
cd recovery-platform/scripts/runtime_image

# 1) Recoverix 전용 initrd 생성 (host initrd 미변경, mkinitramfs -o)
sudo ./20_install_initramfs_hook.sh
# 검증: ls -la /recovery/build/rootfs/boot/initrd.img-*-recoverix

# 2) 사전 점검 (read-only)
./deploy/40_preflight_host_boot.sh

# 3) /boot/recoverix 스테이징 (~1.1GB)
sudo ./deploy/30_stage_host_boot.sh

# 4) GRUB 항목 + update-grub
sudo ./deploy/31_install_grub_entry.sh

# 4b) ESP에 Recoverix 전용 스테이징 (NVRAM/BootOrder/Microsoft EFI 미변경)
sudo ./deploy/40_stage_esp_runtime.sh
sudo ./12_verify_esp_layout.sh

# 5) 재부팅 전 확인
grep -A6 'Recoverix Runtime' /boot/grub/grub.cfg
ls -la /boot/recoverix/
```

## Reboot 전 체크리스트

- [ ] `40_preflight` FAIL 없음
- [ ] `/boot/recoverix/runtime.squashfs` (~1.1GB)
- [ ] `/boot/recoverix/initrd.img-*-recoverix` (recoverix hooks 포함)
- [ ] `/boot/grub/grub.cfg`에 Recoverix 메뉴 2개
- [ ] `GRUB_DEFAULT=0` 유지 (Ubuntu 기본)
- [ ] `GRUB_TIMEOUT=5` — 메뉴 선택 시간 확보
- [ ] Secure Boot **OFF** (첫 테스트)
- [ ] 중요 작업 저장 완료

## 첫 boot test 절차

1. `reboot`
2. 펌웨어 부팅 메뉴 (F12 등) → **`ubuntu`** 선택 (RecoveryBoot 아님)
3. GRUB 메뉴에서 **Ubuntu** (기본, Enter) — 정상 부팅 확인용 1회 권장
4. 재부팅 → **`Recoverix Runtime (immutable overlay)`** 선택 (기본 변경 금지)
5. 실패 시 → GRUB에서 **Ubuntu** 또는 **Windows** 선택

## 부팅 후 검증

```bash
# overlay / readonly
mount | grep -E 'overlay|squashfs'
findmnt -M /

# recoverix boot log (debug 시)
cat /run/recoverix/boot.log 2>/dev/null

# stack
systemctl is-system-running
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; Gtk.init_check(None)"
partclone.ntfs -V 2>/dev/null || partclone -V

# writable / via overlay upper (tmpfs)
touch /tmp/recoverix-write-test && rm /tmp/recoverix-write-test
```

| 항목 | 기대 |
|------|------|
| `/` | overlay, lower squashfs **ro** |
| upper/work | tmpfs |
| systemd | running |
| python3/GTK/partclone | 동작 |
| 재부팅 | overlay 변경 소멸 |

## 실패 시 fallback

| 증상 | 대응 |
|------|------|
| GRUB에 Recoverix 없음 | `sudo ./deploy/31_install_grub_entry.sh` 재실행 |
| kernel panic / 검은 화면 | 재부팅 → GRUB → **Ubuntu** |
| initramfs 조기 종료 | `Recoverix Runtime (debug shell)` + `recoverix.debug=1` |
| overlay 실패 | `dmesg \| grep recoverix`; UUID/cmdline 확인 |
| squashfs not found | `/boot/recoverix/runtime.squashfs` 존재·UUID 일치 확인 |

### initramfs debug

- 메뉴: **Recoverix Runtime (debug shell)**
- cmdline: `recoverix.debug=1` 추가 (템플릿 debug 항목 사용)
- 로그: `/run/recoverix/boot.log`, `dmesg | grep recoverix`

## Rollback

```bash
sudo ./deploy/32_rollback_grub.sh              # GRUB 항목만 제거
sudo ./deploy/32_rollback_grub.sh --remove-artifacts  # + /boot/recoverix 삭제
```

- `/etc/grub.d/41_recoverix` 제거 + `update-grub`
- 백업: `*.recoverix-backup-*` (install 시 생성)
- EFI / shim / Windows: **미변경**

## 위험 요소

| 위험 | 완화 |
|------|------|
| 펌웨어 기본=RecoveryBoot | 테스트 시 **ubuntu** EFI 선택 |
| Host initrd 덮어쓰기 | 별도 `-recoverix` initrd 사용 |
| `/boot` 공간 부족 | `df -h /boot` 확인 (~1.2GB 필요) |
| 구 initrd (handoff 없음) | `20_install_initramfs_hook.sh` 선행 |
| SB ON | 첫 테스트는 SB OFF |

## Secure Boot

- 현재 단계: **SB OFF** 로 테스트
- 향후: 기존 `shim-signed` + `grub-efi-amd64-signed` 체인 유지 (EFI 파일 미교체)

## 수정되는 파일

| 파일 | 작업 |
|------|------|
| `/boot/recoverix/*` | **추가** (initrd-recoverix, squashfs) |
| `/etc/grub.d/41_recoverix` | **추가** |
| `/boot/grub/grub.cfg` | `update-grub`로 **재생성** |
| `/boot/initrd.img-*` (host) | **변경 없음** |
| `/boot/efi/*` shim | **변경 없음** |

## 관련

- [`RUNTIME_BOOT_FLOW.md`](RUNTIME_BOOT_FLOW.md)
- [`RUNTIME_BOOT_READINESS.md`](RUNTIME_BOOT_READINESS.md)

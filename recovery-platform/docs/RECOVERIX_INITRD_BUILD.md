# Recoverix 전용 initrd 생성

## 문제 원인 (정리)

| 오해 | 실제 |
|------|------|
| `20_install`이 `/boot/recoverix/*.recoverix` 생성 | ❌ — **host에 쓰지 않음** |
| `-recoverix` 파일이 없음 | `30_stage_host_boot.sh` **실행 전**에는 host에 없음 (정상) |
| preflight FAIL handoff | 예전엔 `initrd.img-*`(기본명)만 검사 / 미빌드 시 실패 |

**이전 `20` 동작:** rootfs chroot에서 `update-initramfs -u` →  
`/recovery/build/rootfs/boot/initrd.img-<kver>` 만 갱신 (Recoverix hook 포함 가능하나 **파일명이 GRUB이 기대하는 `-recoverix`와 다름**).

**수정 후 `20` 동작:** `mkinitramfs -o /boot/initrd.img-<kver>-recoverix` 만 실행 —  
host `/boot/initrd.img-*` **미접촉**, fingerprint 검증.

## 출력 경로

| 경로 | 설명 |
|------|------|
| `/recovery/build/rootfs/boot/initrd.img-*-recoverix` | rootfs 빌드 산출물 |
| `/recovery/build/runtime/initrd.img-*-recoverix` | 런타임 아티팩트 복사본 |
| `/boot/recoverix/initrd.img-*-recoverix` | `30_stage_host_boot.sh` 후 host 스테이징 |

## 안전한 생성 명령

```bash
cd recovery-platform/scripts/runtime_image
sudo ./20_install_initramfs_hook.sh
```

## 생성 후 검증

```bash
INITRD=/recovery/build/rootfs/boot/initrd.img-6.8.0-117-generic-recoverix
ls -la "$INITRD" /recovery/build/runtime/initrd.img-6.8.0-117-generic-recoverix

lsinitramfs "$INITRD" | grep -E 'recoverix|00-recoverix-handoff'

# 필수 4종
for n in \
  scripts/local-premount/recoverix-overlay \
  scripts/init-bottom/00-recoverix-handoff \
  scripts/recoverix-lib \
  etc/recoverix/runtime.conf; do
  lsinitramfs "$INITRD" | grep -qF "$n" && echo "OK $n" || echo "MISSING $n"
done

# host initrd 미변경
stat /boot/initrd.img-6.8.0-117-generic
```

**Expected:** 4× `OK`, initrd size ~60–70MB, host initrd mtime/size **20 실행 전후 동일**.

## Host 스테이징

```bash
sudo ./deploy/30_stage_host_boot.sh
ls -la /boot/recoverix/
```

## Rollback

- Host: `20`/`30`은 host 기본 initrd를 수정하지 않음 → Ubuntu 부팅 유지
- Recoverix만 제거: `rm -rf /boot/recoverix`
- rootfs 산출물만 재생성: `20` 재실행

## 관련

- [`HOST_FIRST_BOOT_TEST.md`](HOST_FIRST_BOOT_TEST.md)
- [`RUNTIME_BOOT_FLOW.md`](RUNTIME_BOOT_FLOW.md)

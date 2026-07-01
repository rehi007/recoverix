# Recoverix Immutable Runtime — Boot flow

**Phase:** initramfs complete, grub/EFI not connected  
**Host policy:** no EFI/grub/boot-order changes from this pipeline

## Architecture

```text
GRUB (future)
  → vmlinuz + initrd
  → initramfs local-premount: ext4 + squashfs(ro) + overlay(tmpfs)
  → initramfs mountroot: optional root=UUID mount to /root
  → init-bottom 00-recoverix-handoff: rootmnt=/run/rootfs-root, init=/usr/sbin/init
  → run-init → systemd on merged overlay root
```

## Cmdline parameters

| Parameter | Required | Purpose |
|-----------|----------|---------|
| `recoverix.root=1` | Yes (runtime boot) | Enable overlay handoff |
| `root=UUID=…` | Recommended | initramfs device wait + premount fallback |
| `recoverix.uuid=…` | Optional | Same UUID for ext4 premount |
| `recoverix.debug=1` | Optional | printk + `/run/recoverix/boot.log` |
| `ro` | Recommended | Read-only flags for block mounts |

Example (deployment):

```text
root=UUID=xxxxxxxx recoverix.root=1 recoverix.uuid=xxxxxxxx ro
```

## Premount sequence (`local-premount/recoverix-overlay`)

1. Parse cmdline (`recoverix.root`, `recoverix.debug`)
2. `modprobe ext4 squashfs overlay loop`
3. Resolve UUID (`recoverix.uuid` → `root=UUID=` → `RECOVERY_ROOT_UUID` in `runtime.conf`)
4. Wait for `/dev/disk/by-uuid/$UUID`, mount ext4 **ro** at `/run/recoverix-boot`
5. Find `boot/recoverix/runtime.squashfs` on boot mount
6. Mount squashfs **ro,loop** at `/run/rootfs-base`
7. Mount tmpfs upper (`512M`) + work (`64M`)
8. Mount overlay merged root at `/run/rootfs-root`
9. Write state under `/run/recoverix/` (`merged_root`, `squashfs_path`, `boot.log`)

If `recoverix.root=1` and any step fails → **panic** (fail-fast).

## Handoff (`init-bottom/00-recoverix-handoff`)

Runs **before** `udev` init-bottom (name prefix `00-`).

When `recoverix.root=1`:

1. Verify `/run/recoverix/merged_root` is mounted
2. Resolve init: `/usr/sbin/init` (fallback `/sbin/init`)
3. Set `rootmnt=/run/rootfs-root`, `init=/usr/sbin/init`
4. Main `/init` then moves `/run`, `/sys`, `/proc` and **`exec run-init`** into merged root

This replaces the old incomplete `mount --move` in `local-bottom`.

## Immutable guarantees

| Layer | Type | Persistence |
|-------|------|-------------|
| `runtime.squashfs` | ro lower | Immutable across reboots |
| upper/work | tmpfs | Cleared every boot |
| ext4 boot partition | ro mount in premount | No runtime writes to squashfs path |

## Changed files (this phase)

| File | Role |
|------|------|
| `initramfs-hooks/scripts/recoverix-lib` | Shared helpers, debug, UUID |
| `initramfs-hooks/scripts/local-premount/recoverix-overlay` | ext4 + squashfs + overlay |
| `initramfs-hooks/scripts/init-bottom/00-recoverix-handoff` | rootmnt/init redirect |
| `initramfs-hooks/hooks/recoverix-overlay` | modules + binaries in initrd |
| `20_install_initramfs_hook.sh` | install + `update-initramfs -u` |
| `11_verify_runtime_boot.sh` | offline overlay + initrd checks |
| `deploy/grub/recoverix-esp.cfg.template` | RecoveryBoot ESP GRUB (via `40_stage_esp_runtime.sh`) |

Removed: `local-bottom/recoverix-switch-root` (incomplete handoff).

## Rebuild initramfs (rootfs only)

```bash
cd recovery-platform/scripts/runtime_image
sudo ./20_install_initramfs_hook.sh
# or full pipeline:
sudo ./00_build_runtime.sh
```

## Boot test checklist (VM/USB, not host EFI first)

- [ ] `recoverix.debug=1` — check `/run/recoverix/boot.log` after boot
- [ ] `mount | grep overlay` on `/`
- [ ] `findmnt /` → merged overlay, lower squashfs ro
- [ ] `systemctl` runs (PID1 `/usr/sbin/init`)
- [ ] GTK + `partclone.ntfs -V`
- [ ] Reboot → tmpfs upper changes gone

## Risks

| Risk | Mitigation |
|------|------------|
| Wrong UUID | Set `RECOVERY_ROOT_UUID` before deploy; use `recoverix.debug=1` |
| `RECOVERY_ROOT_UUID` placeholder | Replace in `etc/recoverix/runtime.conf` |
| udev `/dev` on `/root` vs merged | handoff runs before udev; run-init moves dev into merged |
| No `root=` on cmdline | Use `recoverix.uuid=` or config UUID |

## Rollback

- **Host:** unchanged (no grub/EFI writes in this phase)
- **Rootfs initramfs:** restore previous `initrd.img-*` backup or re-run install without Recoverix hooks
- **Boot entry:** N/A (not installed yet)

## Related

- [`RUNTIME_BOOT_READINESS.md`](RUNTIME_BOOT_READINESS.md)
- [`IMMUTABLE_RUNTIME_BUILD.md`](IMMUTABLE_RUNTIME_BUILD.md)

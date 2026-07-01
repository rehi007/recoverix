# Recoverix Runtime — Boot readiness audit (pre-grub)

**Phase:** initramfs boot flow complete, **grub/EFI/F5 not connected**  
**Policy:** no host boot modification — see [`RUNTIME_BOOT_FLOW.md`](RUNTIME_BOOT_FLOW.md)

## Current artifact status (observed)

| Artifact | Status |
|----------|--------|
| `runtime.squashfs` ~1.1GB | OK (xz, ~73% compression) |
| `boot/recoverix/runtime.squashfs` | Staged copy in rootfs |
| `vmlinuz` / `initrd` 6.8.0-117-generic | OK |
| initramfs hooks | Present in initrd (`lsinitramfs`) |
| Offline squashfs mount | PASS |
| Offline overlay mount | May FAIL without `modprobe overlay` |

## Architecture verdict

```text
READY_FOR_VM_BOOT_TEST
```

Initramfs premount (ext4 UUID → squashfs → overlay) and `00-recoverix-handoff` (run-init to merged root) are implemented.  
Next: rebuild initrd (`20_install_initramfs_hook.sh`) and VM/USB boot test before host grub connect.

## Critical pre-connect risks

### 1. Squashfs path timing (HIGH)

`local-premount/recoverix-overlay` looks for:

- `/boot/recoverix/runtime.squashfs`

At **local-premount**, the root filesystem (ext4 RECOVERY_LINUX) may **not be mounted yet**, so the file may not exist → overlay skipped silently (exit 0).

**Fix before boot test:** mount boot partition by UUID/LABEL inside initramfs before squashfs mount.

### 2. switch_root handoff (MEDIUM)

`recoverix-switch-root` only runs when `recoverix.root=1` on cmdline and does not fully replace default init flow.

**Fix:** explicit pivot/switch_root to `/run/rootfs-root` or make merged root the initramfs `ROOT` target.

### 3. Build path leakage in squashfs (LOW)

Squashfs contains `/home/for/recoverix/...` from build host paths.

**Fix:** exclude `/home/*` in next `mksquashfs` for production image.

### 4. False WARN on initrd strings (INFO)

Hooks are in initrd (`recoverix-overlay`, `etc/recoverix/`).  
String search for `Recoverix` (capital R) may miss — use `lsinitramfs`.

## Checklist summary

### squashfs contents — PASS

- `/sbin/init` → systemd
- python3, gi/GTK, partclone.ntfs, ntfs-3g, grub tools

### initramfs — PASS with risks

- squashfs + overlay scripts packaged
- **Boot partition mount order** — needs fix

### immutable runtime — PASS (design)

- lower squashfs ro + tmpfs upper → reboot resets session state

### grub — NOT CONNECTED (ready as reference)

- `deploy/grub/recoverix-esp.cfg.template` (RecoveryBoot ESP)
- needs `RECOVERY_ROOT_UUID`, `recoverix.root=1`

### Secure Boot — COMPATIBLE (policy)

- Keep `shim-signed` + `grub-efi-amd64-signed`
- Custom initramfs: review kernel signing / MOK with OEM policy

## Run audit

```bash
cd recovery-platform/scripts/runtime_image
sudo ./12_preflight_boot_readiness.sh
```

Report: `/recovery/build/reports/runtime-build/preflight_boot_readiness_*.txt`

## Boot test checklist (after grub connect)

1. Test in VM/USB first — not host EFI first
2. cmdline: `recoverix.root=1`
3. Confirm `mount | grep overlay` on `/`
4. GTK + partclone smoke test
5. Reboot → overlay changes gone (immutable)

## Related

- [`IMMUTABLE_RUNTIME_BUILD.md`](IMMUTABLE_RUNTIME_BUILD.md)
- [`SECURE_BOOT.md`](SECURE_BOOT.md)

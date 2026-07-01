# Recoverix immutable Recovery Runtime image build

Builds a **bootable runtime image** from `/recovery/build/rootfs` without modifying host Ubuntu boot.

## Architecture

```text
EFI (deployment phase only)
  → grub
    → vmlinuz (KEEP_KERNEL_FLAVOR)
      → initrd (recoverix-overlay hook)
        → squashfs readonly lower (/run/rootfs-base)
        → tmpfs overlay upper/work
        → merged root (/run/rootfs-root)
          → systemd graphical.target
            → gdm3 (autologin recoverix)
              → openbox (XSession via xsessions/openbox.desktop)
                → XDG autostart → recoverix-recovery-ui (GTK)
```

## Host safety (hard rules)

**Forbidden in this directory:**

- `efibootmgr`, `grub-install`, `update-grub`, shim replacement
- Host bootloader / boot order changes

**Allowed:**

- Read/write under `/recovery/build/rootfs`, `/recovery/build/runtime`, reports
- `mksquashfs`, loop mount tests under `/recovery/build/runtime/`
- `update-initramfs` **inside rootfs chroot only**

## Production rootfs (debootstrap)

Default path builds a **minimal Ubuntu 22.04 (jammy) runtime** via `debootstrap` — not a host `/` clone.

- Package seed: `assets/runtime_debootstrap_packages.txt`
- Release map (obsolete → replacement): `assets/runtime_package_release_map.txt` (e.g. jammy: `ntfsprogs` → `ntfs-3g`)
- Validation: `recoverix_validate_runtime_package()` in `lib/runtime_package_validation.sh`
- Bloat guard: `assets/runtime_exclude_packages.txt`
- Target rootfs size: **1–3 GiB** before squashfs
- Source modes logged: `debootstrap` | `snapshot` | `dev-host-rsync`

```bash
cd recovery-platform/scripts/runtime_image
export RECOVERIX_FORCE_DEBOOTSTRAP=1   # first production build or replace host clone
export KEEP_RUNTIME_ROOTFS=0           # optional: free disk after squashfs OK
sudo ./00_build_runtime.sh
```

## Quick start

```bash
cd recovery-platform/scripts/runtime_image
sudo ./00_build_runtime.sh
```

Or step-by-step:

```bash
sudo ./10_build_squashfs.sh
sudo ./20_install_initramfs_hook.sh
sudo ./11_verify_runtime_boot.sh
sudo ./14_verify_backup_finalize_cli.sh
```

One-shot build/deploy/verify with per-step logs:

```bash
cd recovery-platform/scripts/runtime_image
sudo ./run_full_gui_test_cycle.sh
```

Logs are written under:

```text
recovery-platform/scripts/runtime_image/logs/gui-test-cycle/<UTC timestamp>/
```

Key files:

- `summary.txt` — per-step PASS/FAIL and log paths
- `build.log`
- `stage_host.log`
- `stage_esp.log`
- `verify_esp.log`

## Outputs

| Path | Description |
|------|-------------|
| `/recovery/build/runtime/runtime.squashfs` | Readonly runtime base (build output) |
| `/recovery/build/runtime/initrd.img-*-recoverix` | Recoverix initrd (build output) |
| `host:/boot/recoverix/*` | Staged by `deploy/30_stage_host_boot.sh` only — **never** inside rootfs/squashfs |
| `/usr/local/sbin/recoverix-health-check` | Runtime health check (inside squashfs; run after boot) |
| `/usr/local/sbin/recoverix-backup-finalize-check` | Canonical backup finalize JSON check (read-only; `PYTHONPATH=/usr/local/lib/recoverix`) |
| `14_verify_backup_finalize_cli.sh` | Rootfs + squashfs presence check for backup-finalize-check (fails with exit 1 if missing) |

## Build safety (disk / runaway prevention)

| Setting | Default | Meaning |
|---------|---------|---------|
| `RECOVERIX_RUNTIME_BUILD_MODE` | `debootstrap` | Production provision mode |
| `RECOVERIX_FORCE_DEBOOTSTRAP` | `0` | `1` = wipe + rebuild rootfs via debootstrap |
| `KEEP_RUNTIME_ROOTFS` | `1` | `0` = move rootfs aside after successful squashfs |
| `RUNTIME_DEBOOTSTRAP_MIN_FREE_GB` | 12 | Free space required before debootstrap |
| `RUNTIME_ROOTFS_SIZE_TARGET_GB` | 3 | Size goal (warn/fail thresholds in `config.env`) |
| `RUNTIME_BUILD_MIN_FREE_GB` | 8 | Minimum free space on build filesystem |
| `RUNTIME_DISK_WARN_PERCENT` | 90 | WARN threshold |
| `RUNTIME_DISK_FAIL_PERCENT` | 95 | FAIL — build blocked |
| `RUNTIME_DISK_HARD_FAIL_PERCENT` | 99 | HARD FAIL — immediate abort |
| `RECOVERIX_DEV_HOST_RSYNC_BOOTSTRAP` | 0 | **Off by default.** `1` = dev emergency host `/` rsync only |

Host rsync is **not** the production path. Use debootstrap (`RECOVERIX_FORCE_DEBOOTSTRAP=1`) or reuse an existing minimal rootfs (`snapshot`). Host contamination detection is **path-evidence based** (home residue, snap residue, editor/browser residue, machine-id reuse, apt/journal residue), and logs exact offending paths.
`/home/${RECOVERIX_RUNTIME_USER:-recoverix}` and runtime session files (AccountsService/autologin/autostart) are **whitelisted** and are not treated as host contamination.

Machine identity policy:
- Runtime build resets machine identity for first boot regeneration: `/etc/machine-id` truncated and `/var/lib/dbus/machine-id` linked.
- Contamination FAIL for machine-id only when runtime machine-id exactly matches host machine-id.

Apt cache contamination policy:
- Ignore `lock` and `partial/`.
- Evaluate only `*.deb` payload size under `/var/cache/apt/archives`.
- `>300MB`: WARN, `>1GB`: FAIL.

### Production runtime filesystem dependencies (Ubuntu jammy)

| Binary | Package | Notes |
|--------|---------|--------|
| `/usr/bin/ntfs-3g` | `ntfs-3g` | Required |
| `/usr/sbin/mount.ntfs` | `ntfs-3g` | Required (obsolete `ntfsprogs` not used on jammy) |
| `/usr/bin/partclone.ntfs` or `/usr/sbin/partclone.ntfs` | `partclone` | Required |

`ntfsprogs` is **obsolete** on Ubuntu 22.04 jammy and must not appear in the seed list. The release map redirects it to `ntfs-3g` if referenced.

### Runtime apt repository policy (jammy)

Debootstrap `minbase` enables **main only** by default. Production runtime enables explicitly:

- **Components**: `main`, `restricted`, `universe`, `multiverse`
- **Suites**: `jammy`, `jammy-updates`, `jammy-security`
- **Mirror**: `RUNTIME_DEBOOTSTRAP_MIRROR` (default `http://archive.ubuntu.com/ubuntu/`)

**`partclone`** is in **universe** — without universe in rootfs `sources.list`, `apt-get install partclone` fails even when the build host has universe enabled.

Build step: `STEP: configure runtime apt repositories` → `apt-get update` → `apt-cache policy partclone` → report under `reports/runtime-build/runtime_apt_repos_*.txt`.

### Strict minimal runtime purity

- All rootfs `apt-get install` uses `recoverix_runtime_apt_install()` — `--no-install-recommends`, `--no-install-suggests`, deterministic sorted one-by-one install.
- `etc/apt/apt.conf.d/99-recoverix-runtime-minimal` disables recommends globally in rootfs.
- Simulate-before-install blocks **desktop environment** packages (`assets/runtime_desktop_environment_forbidden.txt`).
- **Display-manager dependencies** (`gnome-shell`, `mutter`, `mutter-common`, …) are allowed — see `assets/runtime_gdm3_allowed_dependencies.txt`.
- On purity FAIL: `runtime_excluded_dependency_trace_<timestamp>.txt` (rdepends, apt history, recommending packages).
- Production GUI stack: `recoverix_runtime_apt_install_gdm3` + `assets/runtime_gui_packages.txt` (no `network-manager-gnome`).
- **Ubuntu 22.04 jammy:** no `openbox-session` package — use `openbox` + `/usr/share/xsessions/openbox.desktop`.

### Ubuntu jammy: gdm3 dependency vs GNOME Desktop

Do **not** confuse **display-manager dependencies** with **desktop environment metapackages**.

Jammy `gdm3` pulls a dependency chain that may include `gnome-shell`, `mutter`, and **`evolution-data-server`**. Those libraries support the login manager / GNOME session stack — they are **not** the same as installing `ubuntu-desktop` or `ubuntu-session`.

| Layer | Examples | Recoverix |
|-------|----------|-----------|
| Display-manager deps (allowed) | `gdm3`, `gnome-shell`, `mutter` **or** `mutter-common`, `gnome-session-bin`, optional `evolution-data-server` | Capability-based **PASS** (not exact package identity) |
| Desktop metapackages (forbidden) | `ubuntu-desktop`, `ubuntu-session`, `task-desktop`, `*-desktop`, `snapd` | **FAIL** if installed |
| User session (policy) | — | **openbox** only (`AccountsService Session=openbox`) — not GNOME Shell desktop session |

Recoverix does **not** run a GNOME desktop session. GDM autologins `recoverix` into **openbox**, then Recovery UI autostart.

| Policy | Detail |
|--------|--------|
| APT | `APT::Install-Recommends/Suggests false` + `--no-install-recommends` |
| gdm3 simulate | Desktop metapackages only (`runtime_desktop_environment_forbidden.txt`); DM chain allowlisted |
| Post-install `dpkg -l` | **FAIL** desktop metapackages only; **PASS** GUI boot capabilities |
| Validation style | Runtime-capability based — not exact package name enforcement |

### Jammy package variation (mutter)

On some jammy images **`mutter` is not installed** while **`mutter-common`** satisfies the gnome-shell compositor dependency. The validator accepts **either**:

- `mutter` installed, or
- `mutter-common` installed → `PASS: mutter runtime dependency satisfied (mutter-common)`

Similarly, `evolution-data-server` may be present or absent (WARN only).

### GUI session stack

| Component | Role |
|-----------|------|
| `graphical.target` | Default systemd target after build |
| `gdm3` | Display manager + autologin (`/etc/gdm3/custom.conf`) |
| `openbox` | Window manager; X session file `xsessions/openbox.desktop` |
| AccountsService | `/var/lib/AccountsService/users/recoverix` → `Session=openbox` |
| Recovery UI | `/etc/xdg/autostart/recoverix-recovery-ui.desktop` |
Build-time checks (summary lines include PASS/FAIL per artifact):

```bash
sudo ./00_build_runtime.sh          # installs GUI stack + verify
```

Expected install summary:

```text
--- display-manager dependency chain (allowed) ---
PASS: gdm3
PASS: gnome-shell
PASS: gnome-session-bin
PASS: mutter runtime dependency satisfied (mutter-common)

--- forbidden desktop environment ---
PASS: ubuntu-desktop absent
PASS: ubuntu-session absent
PASS: task-desktop absent

--- session ---
PASS: openbox xsession
PASS: AccountsService Session=openbox
```

### Runtime user/session policy

- Dedicated runtime account is created in rootfs: `recoverix` (`UID=2000`, `GID=2000`, home `/home/recoverix`, shell `/bin/bash`).
- Idempotent build: existing user/group are reconciled to deterministic UID/GID.
- Host user import is not used in production debootstrap runtime.
- Sudo is disabled by default (`RECOVERIX_RUNTIME_ALLOW_SUDO=0`), opt-in by flag only.
- Autologin policy is flag-driven via GDM config (`RECOVERIX_RUNTIME_AUTOLOGIN=1`): `/etc/gdm3/custom.conf`.
- Recovery UI startup method is fixed to desktop autostart: `/etc/xdg/autostart/recoverix-recovery-ui.desktop`.

### GUI failure fallback

1. **GRUB debug entry** — `Recoverix Runtime (debug shell)` uses `systemd.unit=multi-user.target` (no GDM).
2. **Manual recovery** — from TTY (`Ctrl+Alt+F2`):

```bash
id recoverix
cat /etc/gdm3/custom.conf
/usr/local/sbin/recoverix-recovery-ui
```

### graphical.target recovery

```bash
sudo systemctl set-default graphical.target
sudo systemctl enable gdm.service
sudo systemctl start gdm.service
systemctl status gdm.service graphical.target
```

### Xorg troubleshooting

```bash
journalctl -u gdm.service -b --no-pager
tail -n 80 /var/log/Xorg.0.log
lsmod | grep -E 'amdgpu|i915|nouveau'
```

Ensure GPU driver packages match hardware (`xserver-xorg-video-amdgpu` is in the GUI seed list).
| `/recovery/build/reports/runtime-build/` | Build + validation reports |
| `deploy/grub/recoverix-esp.cfg.template` | RecoveryBoot ESP GRUB template |

## Kernel policy

Only `KEEP_KERNEL_FLAVOR` from `config.env` (default `6.8.0-117-generic`). Old kernel references must not appear in artifacts.

## Boot validation (offline)

`11_verify_runtime_boot.sh` loop-mounts squashfs, tests overlay merge, checks GTK/partclone, verifies initrd markers — **does not boot the host**.

## Deployment phase (later)

Host first-boot (additive GRUB only) — see [`docs/HOST_FIRST_BOOT_TEST.md`](../docs/HOST_FIRST_BOOT_TEST.md):

```bash
sudo ./20_install_initramfs_hook.sh
./deploy/40_preflight_host_boot.sh
sudo ./deploy/30_stage_host_boot.sh
sudo ./deploy/31_install_grub_entry.sh
```

Connecting EFI/shim/F5/BootOrder is still a **separate phase** — deploy scripts forbid `grub-install` and `efibootmgr`.

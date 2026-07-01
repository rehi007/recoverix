#!/usr/bin/env bash
# Read-only preflight audit before grub/EFI boot connection (no host boot changes).
# Usage: sudo ./12_preflight_boot_readiness.sh

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/integrity_check.sh
source "${DIR}/lib/integrity_check.sh"
# shellcheck source=lib/initrd_recoverix.sh
source "${DIR}/lib/initrd_recoverix.sh"

require_root
runtime_safety_assert_no_host_boot_mutation "preflight" || die "blocked"

KVER="${KEEP_KERNEL_FLAVOR}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/preflight_boot_readiness_${TS}.txt"
SQ="${RUNTIME_SQUASHFS}"
ROOTFS_STAGED_SQ="${ROOTFS_RESOLVED}/${RUNTIME_BOOT_STAGING}/runtime.squashfs"
HOST_STAGED_SQ="/boot/recoverix/runtime.squashfs"
INITRD="$(recoverix_initrd_resolve_source "$KVER" || echo "")"

PASS=0
WARN=0
FAIL=0
RISK=0

_audit() {
  local level="$1"
  shift
  log "${level}: $*"
  echo "${level}	$*" >> "$REPORT"
  case "$level" in
    PASS) PASS=$((PASS + 1)) ;;
    WARN) WARN=$((WARN + 1)) ;;
    FAIL) FAIL=$((FAIL + 1)) ;;
    RISK) RISK=$((RISK + 1)) ;;
  esac
}

_check_squashfs_contents() {
  _audit INFO "=== 1) runtime.squashfs contents ==="
  [[ -f "$SQ" ]] || { _audit FAIL "missing ${SQ}"; return; }
  _audit PASS "squashfs exists ($(numfmt --to=iec "$(stat -c '%s' "$SQ")" 2>/dev/null || stat -c '%s' "$SQ") bytes)"

  local -a required=(
    "usr/sbin/init"
    "usr/lib/systemd/systemd"
    "usr/bin/python3"
    "usr/sbin/partclone.ntfs"
    "usr/bin/partclone.ntfs"
    "usr/bin/ntfs-3g"
    "usr/sbin/mount.ntfs"
    "usr/lib/x86_64-linux-gnu/libgtk-3.so.0"
    "usr/lib/python3/dist-packages/gi/repository"
    "usr/bin/grub-mkimage"
  )
  local item path
  for item in "${required[@]}"; do
    if unsquashfs -ll "$SQ" 2>/dev/null | grep -q "squashfs-root/${item}"; then
      _audit PASS "squashfs has ${item}"
    else
      _audit FAIL "squashfs missing ${item}"
    fi
  done

  if unsquashfs -ll "$SQ" 2>/dev/null | grep -q 'home/for/recoverix'; then
    _audit RISK "squashfs contains build-path leakage (/home/for/recoverix) — consider exclude before production"
  fi
  if unsquashfs -ll "$SQ" 2>/dev/null | grep -q 'squashfs-root/runtime/recovery-platform'; then
    _audit PASS "recovery-platform tree present in squashfs"
  else
    _audit WARN "recovery-platform path not found under squashfs-root/runtime"
  fi
}

_check_kernel_modules() {
  _audit INFO "=== 2) kernel modules (overlay/squashfs) ==="
  local moddir="${ROOTFS_RESOLVED}/lib/modules/${KVER}"
  for m in fs/overlay/overlay.ko fs/overlay/overlay.ko.zst fs/squashfs/squashfs.ko; do
    if [[ -f "${moddir}/kernel/${m}" ]] || [[ -f "${moddir}/kernel/${m}.zst" ]]; then
      _audit PASS "module ${m}"
    fi
  done
  if zgrep -q overlay "${moddir}/modules.builtin" 2>/dev/null || grep -q overlay "${moddir}/modules.builtin" 2>/dev/null; then
    _audit PASS "overlay in modules.builtin"
  else
    _audit WARN "overlay not listed in modules.builtin (may still load)"
  fi
}

_check_initramfs() {
  _audit INFO "=== 3) initramfs boot flow ==="
  [[ -f "$INITRD" ]] || { _audit FAIL "missing initrd ${INITRD}"; return; }

  if lsinitramfs "$INITRD" 2>/dev/null | grep -q 'scripts/local-premount/recoverix-overlay'; then
    _audit PASS "initrd contains local-premount/recoverix-overlay"
  else
    _audit FAIL "initrd missing recoverix-overlay premount script"
  fi

  if lsinitramfs "$INITRD" 2>/dev/null | grep -q 'scripts/init-bottom/00-recoverix-handoff'; then
    _audit PASS "initrd contains init-bottom/00-recoverix-handoff"
  else
    _audit FAIL "initrd missing 00-recoverix-handoff"
  fi
  if lsinitramfs "$INITRD" 2>/dev/null | grep -q 'scripts/recoverix-lib'; then
    _audit PASS "initrd contains scripts/recoverix-lib"
  else
    _audit WARN "initrd missing recoverix-lib"
  fi

  if lsinitramfs "$INITRD" 2>/dev/null | grep -q 'etc/recoverix/runtime.conf'; then
    _audit PASS "initrd contains etc/recoverix/runtime.conf"
  else
    _audit WARN "initrd missing runtime.conf"
  fi

  if lsinitramfs "$INITRD" 2>/dev/null | grep -qE 'sbin/(mount|switch_root)'; then
    _audit PASS "initrd has mount/switch_root"
  else
    _audit WARN "initrd mount/switch_root not verified"
  fi

  _audit RISK "initramfs premount runs BEFORE root disk mount — /boot/recoverix/runtime.squashfs may be unreachable unless boot partition is mounted first in hook"
  _audit PASS "handoff via init-bottom sets rootmnt to merged overlay (run-init)"
}

_check_overlay_offline() {
  _audit INFO "=== 4) overlay offline test (isolated) ==="
  modprobe overlay 2>/dev/null || true
  modprobe squashfs 2>/dev/null || true

  local base="${RUNTIME_DIR}/preflight-base"
  local upper="${RUNTIME_DIR}/preflight-upper"
  local work="${RUNTIME_DIR}/preflight-work"
  local merged="${RUNTIME_DIR}/preflight-merged"
  rm -rf "${RUNTIME_DIR}/preflight-"*
  mkdir -p "$base" "$upper" "$work" "$merged"

  if [[ ! -f "$SQ" ]]; then
    _audit FAIL "no squashfs for overlay test"
    return
  fi

  if mount -t squashfs -o loop,ro "$SQ" "$base" 2>/dev/null; then
    _audit PASS "offline squashfs loop mount"
    mount -t tmpfs tmpfs "$upper" && mount -t tmpfs tmpfs "$work"
    if mount -t overlay overlay -o "lowerdir=${base},upperdir=${upper},workdir=${work}" "$merged" 2>/dev/null; then
      _audit PASS "offline overlay mount"
      touch "${merged}/.write_test" 2>/dev/null && _audit PASS "overlay upper writable" || _audit WARN "overlay write test failed"
      rm -f "${merged}/.write_test" 2>/dev/null || true
      if [[ -x "${merged}/usr/sbin/init" || -x "${merged}/sbin/init" ]]; then
        _audit PASS "merged root has init (usr/sbin/init or sbin/init)"
      else
        _audit WARN "merged root missing init symlink — switch_root checks /sbin/init today"
      fi
      umount "$merged" 2>/dev/null || true
    else
      _audit FAIL "offline overlay mount failed (modprobe overlay? separate tmpfs for work/upper?)"
      dmesg 2>/dev/null | tail -3 >> "$REPORT" || true
    fi
    umount "$work" "$upper" "$base" 2>/dev/null || true
  else
    _audit FAIL "offline squashfs mount failed"
  fi
}

_check_grub_readiness() {
  local esp_tpl="${DIR}/deploy/grub/recoverix-esp.cfg.template"
  # shellcheck source=deploy/lib/grub_boot_chain.sh
  source "${DIR}/deploy/lib/grub_boot_chain.sh"

  _audit INFO "=== 5) grub boot readiness (reference only) ==="
  _audit PASS "grub template: ${esp_tpl} (ESP/RecoveryBoot only)"
  if [[ -f "$esp_tpl" ]] && recoverix_grub_validate_xorg_forensic_menuentry "$esp_tpl"; then
    _audit PASS "xorg forensic GRUB A/B: GUI=graphical.target console=multi-user.target"
  else
    _audit FAIL "xorg forensic GRUB A/B template invalid (GUI + console menuentries)"
  fi
  if [[ -f "$esp_tpl" ]] && grep -qF '/boot/recoverix/vmlinuz-' "$esp_tpl" && \
     grep -qF '/boot/recoverix/initrd.img-' "$esp_tpl"; then
    _audit PASS "ESP grub template loads kernel/initrd from /boot/recoverix (Recovery Linux)"
  else
    _audit FAIL "ESP grub template must use /boot/recoverix vmlinuz+initrd paths"
  fi
  [[ -f "${ROOTFS_RESOLVED}/boot/vmlinuz-${KVER}" ]] && _audit PASS "vmlinuz present" || _audit FAIL "vmlinuz missing"
  [[ -f "$INITRD" ]] && _audit PASS "initrd present" || _audit FAIL "initrd missing"
  [[ -f "/boot/recoverix/vmlinuz-${KVER}" ]] && _audit PASS "host staged vmlinuz /boot/recoverix" || \
    _audit WARN "host /boot/recoverix/vmlinuz missing (run deploy/30_stage_host_boot.sh)"
  if [[ -f "$ROOTFS_STAGED_SQ" ]]; then
    _audit FAIL "rootfs must not contain ${RUNTIME_BOOT_STAGING}/runtime.squashfs (recursive squashfs risk)"
  else
    _audit PASS "no squashfs staged inside rootfs"
  fi
  [[ -f "$SQ" ]] && _audit PASS "build output squashfs ${SQ}" || _audit FAIL "missing ${SQ}"
  [[ -f "$HOST_STAGED_SQ" ]] && _audit PASS "host staged ${HOST_STAGED_SQ}" || _audit WARN "host ${HOST_STAGED_SQ} missing (run deploy/30_stage_host_boot.sh)"

  _audit RISK "grub must use recoverix.root=1 and recoverix.uuid=RECOVERY_LINUX_UUID (p4)"
  _audit RISK "ESP bootloader-only: no vmlinuz/initrd on EFI/Recoverix; use /boot/recoverix on Recovery Linux"
  _audit RISK "host /boot/recoverix/runtime.squashfs required at boot (not inside squashfs rootfs)"
}

_check_secure_boot() {
  _audit INFO "=== 6) Secure Boot compatibility (review) ==="
  local pkg
  for pkg in shim-signed grub-efi-amd64-signed grub-efi-amd64-bin linux-image-${KVER}; do
    if host_dpkg_query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q installed; then
      _audit PASS "package installed: ${pkg}"
    else
      _audit WARN "package missing in rootfs: ${pkg}"
    fi
  done
  _audit RISK "custom initramfs may require signed kernel policy review (MOK or vendor DB)"
  _audit PASS "shim chain not modified in this phase — deployment can keep stock shim+signed grub"
}

_check_boot_risks() {
  _audit INFO "=== 7) pre-connect risk summary ==="
  _audit RISK "boot loop if recoverix.root=1 set but overlay mount fails — hook exits 0 silently"
  _audit RISK "initramfs panic if merged root has no valid init and switch_root attempted incorrectly"
  _audit RISK "squashfs path not found if boot partition unmounted during premount"
  _audit PASS "squashfs only in ${RUNTIME_DIR} until host deploy copies to /boot/recoverix"
}

_write_recommendations() {
  {
    echo ""
    echo "=== 8) recommendations before grub connect ==="
    echo "1. Fix initramfs: mount boot partition (UUID/LABEL) before squashfs path access"
    echo "2. Complete switch_root/pivot to /run/rootfs-root when recoverix.root=1"
    echo "3. Re-run: sudo ./11_verify_runtime_boot.sh after modprobe overlay"
    echo "4. Exclude /home/* build paths from next squashfs rebuild for production"
    echo "5. Define RECOVERY_LINUX ext4 layout: boot/, recoverix/runtime.squashfs, optional EFI/"
    echo "6. Secure Boot: keep shim-signed + grub-efi-amd64-signed; plan kernel signing/MOK"
    echo ""
    echo "=== 9) boot test checklist (when connecting grub) ==="
    echo "[ ] Boot from USB/VM with separate disk image first"
    echo "[ ] cmdline includes recoverix.root=1"
    echo "[ ] serial console / initramfs debug if panic"
    echo "[ ] confirm overlay on / after boot"
    echo "[ ] python3-gi Gtk window test"
    echo "[ ] partclone.ntfs -V"
    echo "[ ] reboot resets overlay tmpfs (immutable)"
    echo ""
    echo "=== verdict ==="
  } >> "$REPORT"

  if [[ $FAIL -eq 0 && $RISK -le 4 ]]; then
    echo "overall: READY_WITH_FIXES — core artifacts OK; initramfs boot path timing needs hardening" >> "$REPORT"
    _audit PASS "overall READY_WITH_FIXES (see RISK items)"
  elif [[ $FAIL -le 2 ]]; then
    echo "overall: NEEDS_WORK" >> "$REPORT"
    _audit WARN "overall NEEDS_WORK"
  else
    echo "overall: NOT_READY" >> "$REPORT"
    _audit FAIL "overall NOT_READY"
  fi

  echo "counts: PASS=${PASS} WARN=${WARN} FAIL=${FAIL} RISK=${RISK}" >> "$REPORT"
}

log "=== Preflight boot readiness audit (read-only) ==="
{
  echo "=== Recoverix Preflight Boot Readiness ==="
  echo "timestamp: ${TS}"
  echo "host_boot_modified: false"
  echo "squashfs: ${SQ}"
  echo "kernel: ${KVER}"
  echo
} > "$REPORT"

_check_squashfs_contents
_check_kernel_modules
_check_initramfs
_check_overlay_offline
_check_grub_readiness
_check_secure_boot
_check_boot_risks
_write_recommendations

log "report: ${REPORT}"
log "PASS=${PASS} WARN=${WARN} FAIL=${FAIL} RISK=${RISK}"

[[ $FAIL -gt 0 ]] && exit 2
[[ $WARN -gt 0 || $RISK -gt 6 ]] && exit 1
exit 0

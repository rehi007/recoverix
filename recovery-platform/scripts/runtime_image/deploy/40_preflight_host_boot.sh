#!/usr/bin/env bash
# Read-only preflight before host GRUB connect (no modifications).
# Usage: ./40_preflight_host_boot.sh

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RI_DIR="$(cd "${DIR}/.." && pwd)"
# shellcheck source=../lib/common.sh
source "${RI_DIR}/lib/common.sh"
# shellcheck source=../lib/initrd_recoverix.sh
source "${RI_DIR}/lib/initrd_recoverix.sh"

KVER="${KEEP_KERNEL_FLAVOR}"
PASS=0
FAIL=0
WARN=0

ok() { printf 'PASS\t%s\n' "$*"; PASS=$((PASS + 1)); }
warn() { printf 'WARN\t%s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf 'FAIL\t%s\n' "$*"; FAIL=$((FAIL + 1)); }

echo "=== Recoverix host boot preflight ==="
echo "root_uuid: $(findmnt -no UUID / 2>/dev/null || echo unknown)"
echo "efi: $([ -d /sys/firmware/efi ] && echo yes || echo no)"
echo "boot_current: $(efibootmgr 2>/dev/null | grep BootCurrent || echo unknown)"
echo

INITRD_SRC="$(recoverix_initrd_resolve_source "$KVER" || true)"
if [[ -n "$INITRD_SRC" ]]; then
  ok "recoverix initrd exists: ${INITRD_SRC}"
  if recoverix_initrd_is_legacy_name "$INITRD_SRC"; then
    warn "legacy initrd name (re-run 20_install for initrd.img-*-recoverix)"
  fi
  if recoverix_verify_initrd_hooks "$INITRD_SRC"; then
    ok "recoverix initrd hooks verified"
  else
    fail "recoverix initrd missing hooks — run 20_install_initramfs_hook.sh"
  fi
else
  fail "no recoverix initrd (run 20_install_initramfs_hook.sh)"
fi

[[ -f "$(recoverix_initrd_host_path "$KVER")" ]] && ok "host staged initrd" || \
  warn "host /boot/recoverix initrd not staged yet (run 30_stage_host_boot.sh)"

[[ -f "${RUNTIME_SQUASHFS}" ]] && ok "runtime.squashfs exists" || fail "runtime.squashfs missing"
[[ -f "/boot/vmlinuz-${KVER}" ]] && ok "host vmlinuz ${KVER}" || warn "host vmlinuz missing (will stage from rootfs)"

avail_kb="$(df --output=avail /boot 2>/dev/null | tail -1 | tr -d ' ')"
if [[ -n "$avail_kb" && "$avail_kb" -gt 1500000 ]]; then
  ok "/boot free space sufficient (~$((avail_kb / 1024))MB)"
else
  warn "/boot may lack space for 1.1GB squashfs"
fi

[[ -f /etc/grub.d/41_recoverix ]] && warn "41_recoverix already installed" || ok "grub snippet not yet installed"
grep -q '^GRUB_DEFAULT=0' /etc/default/grub 2>/dev/null && ok "GRUB_DEFAULT=0 (Ubuntu first)" || \
  warn "GRUB_DEFAULT not 0 — verify default entry manually"

echo
echo "summary: PASS=${PASS} WARN=${WARN} FAIL=${FAIL}"
[[ $FAIL -gt 0 ]] && exit 2
[[ $WARN -gt 0 ]] && exit 1
exit 0

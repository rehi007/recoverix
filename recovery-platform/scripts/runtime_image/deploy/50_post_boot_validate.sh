#!/usr/bin/env bash
# Run INSIDE Recoverix Runtime after first boot (read-only checks).
# Usage: sudo ./50_post_boot_validate.sh

set -euo pipefail

PASS=0
WARN=0
FAIL=0

ok() { printf 'PASS\t%s\n' "$*"; PASS=$((PASS + 1)); }
warn() { printf 'WARN\t%s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf 'FAIL\t%s\n' "$*"; FAIL=$((FAIL + 1)); }

echo "=== Recoverix Runtime post-boot validation ==="
echo "timestamp: $(date -u +%Y%m%dT%H%M%SZ)"
echo "cmdline: $(cat /proc/cmdline)"
echo

if grep -q 'recoverix.root=1' /proc/cmdline 2>/dev/null; then
  ok "cmdline has recoverix.root=1"
else
  warn "recoverix.root=1 not on cmdline — may be host Ubuntu, not Recoverix runtime"
fi

if [[ -f /run/recoverix/boot.log ]]; then
  ok "/run/recoverix/boot.log exists"
  echo "--- boot.log (last 20 lines) ---"
  tail -20 /run/recoverix/boot.log
  echo "---"
else
  warn "no /run/recoverix/boot.log"
fi

if mount | grep -q ' on / type overlay'; then
  ok "root (/) is overlay"
else
  fail "root is not overlay — $(findmnt -no FSTYPE / 2>/dev/null || echo unknown)"
fi

if mount | grep -qE 'type squashfs.*ro|squashfs.*\(ro'; then
  ok "squashfs ro layer present"
else
  warn "squashfs ro layer not obvious in mount table"
fi

if mount | grep -qE 'rootfs-overlay|rootfs-work'; then
  ok "tmpfs upper/work paths visible"
else
  warn "rootfs-overlay/work not in mount output (may use different paths)"
fi

findmnt -T / >/dev/null 2>&1 && ok "findmnt / works: $(findmnt -no FSTYPE /)" || warn "findmnt / failed"

if systemctl is-system-running >/dev/null 2>&1; then
  ok "systemd: $(systemctl is-system-running)"
else
  fail "systemd not running"
fi

if python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; Gtk.init_check(None)" 2>/dev/null; then
  ok "GTK import"
else
  fail "GTK import failed"
fi

if partclone.ntfs -V >/dev/null 2>&1; then
  ok "partclone.ntfs: $(partclone.ntfs -V 2>&1 | head -1)"
elif partclone -V >/dev/null 2>&1; then
  ok "partclone: $(partclone -V 2>&1 | head -1)"
else
  fail "partclone not executable"
fi

if [[ -d /recovery/image ]]; then
  ok "/recovery/image directory exists"
  ls -la /recovery/image 2>/dev/null | head -5 || true
else
  warn "/recovery/image missing (may need separate mount)"
fi

echo
echo "=== mount summary ==="
mount | grep -E 'overlay|squashfs|/run/rootfs' || true
echo
echo "summary: PASS=${PASS} WARN=${WARN} FAIL=${FAIL}"
[[ $FAIL -eq 0 ]] && exit 0
exit 2

#!/usr/bin/env bash
# Install Recoverix initramfs hooks and build Recoverix-only initrd (rootfs chroot).
# Usage: sudo ./20_install_initramfs_hook.sh
#
# Output (never touches host /boot/initrd.img-*):
#   ${RUNTIME_DIR}/initrd.img-<kver>-recoverix
#
# Transient during build only (removed before exit):
#   ${ROOTFS}/boot/initrd.img-<kver>-recoverix
#
# Forbidden on host: update-grub, grub-install, efibootmgr, update-initramfs.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/initrd_recoverix.sh
source "${DIR}/lib/initrd_recoverix.sh"
# shellcheck source=lib/integrity_check.sh
source "${DIR}/lib/integrity_check.sh"

require_root
runtime_safety_assert_no_host_boot_mutation "install" || die "safety"

KVER="${KEEP_KERNEL_FLAVOR}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT="${REPORT_DIR}/initramfs_install_${TS}.txt"
INITRD_ROOTFS="$(recoverix_initrd_rootfs_path "$KVER")"
INITRD_RUNTIME="$(recoverix_initrd_runtime_path "$KVER")"
HOST_FP_BEFORE="$(host_initrd_fingerprint "$KVER" || true)"

log "=== Install Recoverix initramfs hooks + build recoverix initrd ==="
log "transient rootfs path: ${INITRD_ROOTFS}"
log "runtime artifact: ${INITRD_RUNTIME}"
log "host initrd fingerprint (must be unchanged): ${HOST_FP_BEFORE:-N/A}"

{
  echo "=== Initramfs hook install ==="
  echo "timestamp: ${TS}"
  echo "kernel: ${KVER}"
  echo "rootfs: ${ROOTFS_RESOLVED}"
  echo "transient_rootfs_initrd: ${INITRD_ROOTFS}"
  echo "output_runtime: ${INITRD_RUNTIME}"
  echo "host_initrd_fingerprint_before: ${HOST_FP_BEFORE}"
  echo "uses_update_initramfs_on_host: false"
  echo "uses_mkinitramfs_o: true"
} > "$REPORT"

# squashfs is NOT copied into rootfs (prevents recursive embedding in next squashfs build).
# Host/runtime artifacts: ${RUNTIME_SQUASHFS}, deploy/30_stage_host_boot.sh -> /boot/recoverix/

mkdir -p "${ROOTFS_RESOLVED}/etc/recoverix"
install -m 0644 "${DIR}/overlay/recoverix-overlay-paths.conf" \
  "${ROOTFS_RESOLVED}/etc/recoverix/overlay-paths.conf"
cat > "${ROOTFS_RESOLVED}/etc/recoverix/runtime.conf" <<EOF
# Recoverix immutable runtime (generated ${TS})
KEEP_KERNEL_FLAVOR="${KVER}"
RECOVERY_ROOT_UUID=RECOVERY_ROOT_UUID
RECOVERIX_SQUASHFS_PATH=/boot/recoverix/runtime.squashfs
RECOVERIX_SQUASHFS_LABEL="${RUNTIME_SQUASHFS_LABEL}"
RECOVERIX_BASE_MOUNT=/run/rootfs-base
RECOVERIX_OVERLAY_UPPER=/run/recoverix-overlay/upper
RECOVERIX_OVERLAY_WORK=/run/recoverix-overlay/work
RECOVERIX_MERGED_ROOT=/mnt/rootfs-root
EOF

install -d "${ROOTFS_RESOLVED}/etc/initramfs-tools/hooks"
install -d "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/init-top"
install -d "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/local-top"
install -d "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/local-premount"
install -d "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/init-bottom"
install -m 0755 "${DIR}/initramfs-hooks/hooks/recoverix-overlay" \
  "${ROOTFS_RESOLVED}/etc/initramfs-tools/hooks/recoverix-overlay"
install -m 0755 "${DIR}/initramfs-hooks/scripts/recoverix-lib" \
  "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/recoverix-lib"
install -m 0755 "${DIR}/initramfs-hooks/scripts/init-top/00-recoverix-rootdir" \
  "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/init-top/00-recoverix-rootdir"
install -m 0755 "${DIR}/initramfs-hooks/scripts/init-top/01-recoverix-tty-quiet" \
  "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/init-top/01-recoverix-tty-quiet"
install -m 0755 "${DIR}/initramfs-hooks/scripts/local-top/00-recoverix-root-tmpfs" \
  "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/local-top/00-recoverix-root-tmpfs"
install -m 0755 "${DIR}/initramfs-hooks/scripts/local-premount/recoverix-overlay" \
  "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/local-premount/recoverix-overlay"
install -m 0755 "${DIR}/initramfs-hooks/scripts/init-bottom/00-recoverix-handoff" \
  "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/init-bottom/00-recoverix-handoff"
rm -f "${ROOTFS_RESOLVED}/etc/initramfs-tools/scripts/local-bottom/recoverix-switch-root" 2>/dev/null || true

echo "hooks_installed: recoverix-overlay recoverix-lib 00-recoverix-rootdir 01-recoverix-tty-quiet 00-recoverix-root-tmpfs recoverix-overlay 00-recoverix-handoff" >> "$REPORT"

# Chroot mkinitramfs -o ONLY (no update-initramfs — avoids overwriting default initrd name)
mkdir -p "${ROOTFS_RESOLVED}/dev" "${ROOTFS_RESOLVED}/proc" "${ROOTFS_RESOLVED}/sys"
mount -o bind /dev "${ROOTFS_RESOLVED}/dev" 2>/dev/null || true
mount -o bind /dev/pts "${ROOTFS_RESOLVED}/dev/pts" 2>/dev/null || true
mount -t proc proc "${ROOTFS_RESOLVED}/proc" 2>/dev/null || true
mount -t sysfs sysfs "${ROOTFS_RESOLVED}/sys" 2>/dev/null || true

OUT_IN_CHROOT="/boot/$(recoverix_initrd_basename "$KVER")"
log "mkinitramfs -o ${OUT_IN_CHROOT} (inside rootfs chroot)..."
set +e
chroot "${ROOTFS_RESOLVED}" /bin/bash -lc \
  "DEBIAN_FRONTEND=noninteractive mkinitramfs -o '${OUT_IN_CHROOT}' '${KVER}'" 2>&1 | tee -a "$REPORT"
MKINIT_RC=${PIPESTATUS[0]}
set -e

for m in sys proc dev/pts dev; do
  umount "${ROOTFS_RESOLVED}/${m}" 2>/dev/null || true
done

if [[ $MKINIT_RC -ne 0 ]]; then
  echo "FAIL	mkinitramfs rc=${MKINIT_RC}" >> "$REPORT"
  die "mkinitramfs failed in rootfs (rc=${MKINIT_RC})"
fi
echo "PASS	mkinitramfs rc=0" >> "$REPORT"

if [[ ! -f "$INITRD_ROOTFS" ]]; then
  echo "FAIL	missing transient initrd ${INITRD_ROOTFS}" >> "$REPORT"
  die "missing recoverix initrd after mkinitramfs: ${INITRD_ROOTFS}"
fi

# Verify hooks on the built initrd before moving/removing it from rootfs.
if ! recoverix_verify_initrd_hooks "$INITRD_ROOTFS" "$REPORT"; then
  echo "FAIL	initrd hook verification" >> "$REPORT"
  die "recoverix initrd hook verification failed — see ${REPORT}"
fi
echo "PASS	initrd hook verification" >> "$REPORT"

install -m 0644 "$INITRD_ROOTFS" "$INITRD_RUNTIME"
log "artifact copy -> ${INITRD_RUNTIME}"
rm -f "$INITRD_ROOTFS"
log "INFO: removed transient initrd from rootfs boot (anti-recursive policy)"

if [[ ! -f "$INITRD_RUNTIME" ]]; then
  echo "FAIL	missing runtime initrd ${INITRD_RUNTIME}" >> "$REPORT"
  die "missing runtime initrd artifact: ${INITRD_RUNTIME}"
fi

runtime_purge_rootfs_boot_staging
if ! runtime_verify_rootfs_no_staged_artifacts; then
  echo "FAIL	rootfs staged-artifact guard" >> "$REPORT"
  die "rootfs still contains staged boot artifacts after initrd build — abort"
fi
echo "PASS	rootfs staged-artifact guard" >> "$REPORT"
echo "rootfs_staging_policy: squashfs_and_initrd_not_in_rootfs" >> "$REPORT"

HOST_FP_AFTER="$(host_initrd_fingerprint "$KVER" || true)"
echo "host_initrd_fingerprint_after: ${HOST_FP_AFTER}" >> "$REPORT"
if ! assert_host_initrd_unchanged "$KVER" "$HOST_FP_BEFORE"; then
  echo "FAIL	host initrd fingerprint changed" >> "$REPORT"
  die "host /boot/initrd.img-${KVER} was modified — abort for safety"
fi
echo "PASS	host initrd unchanged" >> "$REPORT"

{
  echo "initrd_bytes: $(stat -c '%s' "${INITRD_RUNTIME}")"
  echo "status: OK"
} >> "$REPORT"

log "PASS: recoverix initrd OK: ${INITRD_RUNTIME}"
log "INFO: host /boot/initrd.img-${KVER} unchanged"
log "Next host staging: sudo ./deploy/30_stage_host_boot.sh"
log "report: ${REPORT}"
exit 0

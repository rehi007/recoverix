#!/usr/bin/env bash
# Rollback snapshot: tar rootfs (from host, never includes host / bind mounts if unmounted).
# Usage: sudo ./06_snapshot_rootfs.sh <tag>
# Restore: see docs/ROOTFS_MINIMIZE.md

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
require_root

TAG="${1:-snapshot}"
OUT_DIR="/recovery/build/snapshots"
OUT="${OUT_DIR}/rootfs_${TAG}.tar.zst"

mkdir -p "$OUT_DIR"
if chroot_is_mounted; then
  die "unmount chroot first: sudo ${DIR}/99_chroot_leave.sh"
fi

log "Creating snapshot ${OUT} (this may take several minutes)"
tar --xattrs --acls -C "$(dirname "${ROOTFS_RESOLVED}")" \
  --exclude='./proc' --exclude='./sys' --exclude='./dev' --exclude='./run' \
  -cf - "$(basename "${ROOTFS_RESOLVED}")" | zstd -T0 -19 -o "$OUT"
log "Wrote ${OUT}"
log "Restore: rm -rf ${ROOTFS_RESOLVED} && mkdir -p ${ROOTFS_RESOLVED} && tar -C $(dirname ${ROOTFS_RESOLVED}) -xf - | zstd -d ${OUT} ..."

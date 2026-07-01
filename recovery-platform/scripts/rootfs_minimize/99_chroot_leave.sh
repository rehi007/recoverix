#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
require_root
umount_chroot_fs
log "chroot mounts released for ${ROOTFS_RESOLVED}"

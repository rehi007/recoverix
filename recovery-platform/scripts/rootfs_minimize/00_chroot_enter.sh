#!/usr/bin/env bash
# Enter an interactive shell inside the Recovery rootfs (host-safe chroot).
# Usage: sudo ./00_chroot_enter.sh

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
require_root

log "ROOTFS=${ROOTFS_RESOLVED}"
log "Host protection: this script never chroots /"
mount_chroot_fs
export PS1="recoverix-rootfs# "
log "Entering chroot. Type 'exit' to leave, then run umount via 99_chroot_leave.sh if needed."
exec chroot "${ROOTFS_RESOLVED}" /bin/bash --login

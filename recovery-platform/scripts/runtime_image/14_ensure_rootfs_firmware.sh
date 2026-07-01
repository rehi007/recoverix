#!/usr/bin/env bash
# Copy host AMDGPU firmware into /recovery/build/rootfs (no chroot apt).
# Usage: sudo ./14_ensure_rootfs_firmware.sh
#
# Does NOT modify host boot chain, initramfs, EFI, or GRUB.

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/common.sh
source "${DIR}/lib/common.sh"
# shellcheck source=lib/integrity_check.sh
source "${DIR}/lib/integrity_check.sh"

require_root
runtime_safety_assert_no_host_boot_mutation "firmware" || die "safety check"

log "=== Ensure rootfs AMDGPU firmware ==="
runtime_ensure_amdgpu_firmware
log "Done."
